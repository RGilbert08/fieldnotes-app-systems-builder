"""Tenant-scoped period schedules and educator pacing-drift reports."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth import AuthenticationService, InvalidSession, extract_session_token
from backend.authentication.login_gate import SESSION_COOKIE_NAME, get_database_session
from backend.models import (
    ClassStreamLedger,
    ClassroomPacingDriftReport,
    ClassroomPeriodSchedule,
    SchoolTenant,
    User,
    UserRole,
    utc_now,
)

router = APIRouter(tags=["classroom periods"])
JHB_TIME_ZONE = ZoneInfo("Africa/Johannesburg")
DRIFT_REASONS = {
    "LOAD_SHEDDING",
    "UNEXPECTED_ASSEMBLY",
    "REMEDIAL_EXTENSION",
    "EXTRA_MURAL_OVERRUN",
}


class PeriodSlotInput(BaseModel):
    weekday: int = Field(ge=1, le=7)
    period_number: int = Field(ge=1, le=20)
    label: str = Field(min_length=1, max_length=80)
    start_time: time
    end_time: time

    @field_validator("label")
    @classmethod
    def trim_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Period labels cannot be blank.")
        return value

    @model_validator(mode="after")
    def validate_time_order(self):
        if self.end_time <= self.start_time:
            raise ValueError("A period must end after it starts.")
        return self


class PeriodScheduleUpdate(BaseModel):
    slots: list[PeriodSlotInput] = Field(max_length=140)

    @model_validator(mode="after")
    def validate_slots(self):
        keys = set()
        by_day: dict[int, list[PeriodSlotInput]] = {}
        for slot in self.slots:
            key = (slot.weekday, slot.period_number)
            if key in keys:
                raise ValueError("Period numbers must be unique for each weekday.")
            keys.add(key)
            by_day.setdefault(slot.weekday, []).append(slot)
        for day_slots in by_day.values():
            ordered = sorted(day_slots, key=lambda slot: slot.start_time)
            for previous, current in zip(ordered, ordered[1:]):
                if current.start_time < previous.end_time:
                    raise ValueError("Period slots cannot overlap.")
        return self


class DriftReportInput(BaseModel):
    drift_key: str = Field(min_length=1, max_length=180)
    period_slot_key: str = Field(min_length=1, max_length=32)
    period_number: int = Field(ge=1, le=20)
    period_label: str = Field(min_length=1, max_length=80)
    period_date: date
    period_start_time: time
    period_end_time: time
    class_identifier: str = Field(min_length=1, max_length=80)
    grade: str = Field(default="", max_length=32)
    subject: str = Field(default="", max_length=160)
    lesson_title: str = Field(min_length=1, max_length=180)
    reason: Literal[
        "LOAD_SHEDDING",
        "UNEXPECTED_ASSEMBLY",
        "REMEDIAL_EXTENSION",
        "EXTRA_MURAL_OVERRUN",
    ]
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @field_validator("drift_key", "period_slot_key", "period_label", "class_identifier", "grade", "subject", "lesson_title")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_period(self):
        if not self.period_label or not self.lesson_title or not self.class_identifier:
            raise ValueError("A class, period label, and lesson title are required.")
        if self.period_end_time <= self.period_start_time:
            raise ValueError("A period must end after it starts.")
        return self


def _actor(request: Request, session: Session) -> User:
    token = extract_session_token(
        request.headers.get("Authorization"),
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if not token:
        raise InvalidSession("A valid session is required.")
    return AuthenticationService(session).authenticate(token).user


def _tenant(session: Session, actor: User) -> SchoolTenant:
    return AuthenticationService(session).resolve_active_school_tenant(actor)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _time_text(value: time) -> str:
    return value.strftime("%H:%M")


def _serialize_slots(slots: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        (dict(slot) for slot in slots if isinstance(slot, dict)),
        key=lambda slot: (
            int(slot.get("weekday", 0)),
            int(slot.get("period_number", 0)),
        ),
    )


def get_schedule(
    session: Session,
    tenant_id: int,
) -> ClassroomPeriodSchedule | None:
    return session.scalar(
        select(ClassroomPeriodSchedule).where(
            ClassroomPeriodSchedule.tenant_id == tenant_id
        )
    )


def local_clock(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(JHB_TIME_ZONE)


def find_active_period_slot(
    schedule: ClassroomPeriodSchedule | None,
    slot_key: str,
    now: datetime | None = None,
) -> tuple[dict[str, object], datetime] | None:
    local_now = local_clock(now)
    for slot in (schedule.slots if schedule else []):
        if not isinstance(slot, dict):
            continue
        expected_key = f"{slot.get('weekday')}:{slot.get('period_number')}"
        if expected_key != slot_key or int(slot.get("weekday", 0)) != local_now.isoweekday():
            continue
        try:
            start_time = time.fromisoformat(str(slot["start_time"]))
            end_time = time.fromisoformat(str(slot["end_time"]))
        except (KeyError, TypeError, ValueError):
            continue
        now_time = local_now.time().replace(tzinfo=None)
        if start_time <= now_time < end_time:
            end_local = datetime.combine(local_now.date(), end_time, JHB_TIME_ZONE)
            end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
            return slot, end_utc
    return None


def _schedule_payload(schedule: ClassroomPeriodSchedule | None) -> dict[str, object]:
    return {
        "revision": schedule.revision if schedule else 0,
        "timezone": "Africa/Johannesburg",
        "slots": _serialize_slots(schedule.slots if schedule else []),
        "updated_at": schedule.updated_at if schedule else None,
    }


def _drift_payload(report: ClassroomPacingDriftReport, educator_name: str) -> dict[str, object]:
    return {
        "id": report.id,
        "drift_key": report.drift_key,
        "educator_name": educator_name,
        "period_slot_key": report.period_slot_key,
        "period_number": report.period_number,
        "period_label": report.period_label,
        "period_date": report.period_date.isoformat(),
        "period_start_time": report.period_start_time,
        "period_end_time": report.period_end_time,
        "class_identifier": report.class_identifier,
        "grade": report.grade,
        "subject": report.subject,
        "lesson_title": report.lesson_title,
        "reason": report.reason,
        "started_at": report.started_at,
        "completed_at": report.completed_at,
        "reported_at": report.reported_at,
    }


@router.get("/api/classroom/period-schedule", status_code=status.HTTP_200_OK)
def read_period_schedule(
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
) -> dict[str, object]:
    _no_store(response)
    actor = _actor(request, session)
    AuthenticationService(session).require_roles(
        actor, UserRole.EDUCATOR, UserRole.PRINCIPAL
    )
    tenant = _tenant(session, actor)
    return _schedule_payload(get_schedule(session, tenant.id))


@router.put("/api/admin/classroom-period-schedule", status_code=status.HTTP_200_OK)
def update_period_schedule(
    payload: PeriodScheduleUpdate,
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
) -> dict[str, object]:
    _no_store(response)
    actor = _actor(request, session)
    AuthenticationService(session).require_roles(actor, UserRole.PRINCIPAL)
    tenant = _tenant(session, actor)
    slots = [
        {
            "slot_key": f"{slot.weekday}:{slot.period_number}",
            "weekday": slot.weekday,
            "period_number": slot.period_number,
            "label": slot.label,
            "start_time": _time_text(slot.start_time),
            "end_time": _time_text(slot.end_time),
        }
        for slot in payload.slots
    ]
    schedule = get_schedule(session, tenant.id)
    if schedule is None:
        schedule = ClassroomPeriodSchedule(
            tenant_id=tenant.id,
            revision=1,
            slots=slots,
            updated_by_user_id=actor.id,
        )
        session.add(schedule)
    else:
        schedule.revision += 1
        schedule.slots = slots
        schedule.updated_by_user_id = actor.id
        schedule.updated_at = utc_now()
    session.commit()
    session.refresh(schedule)
    return _schedule_payload(schedule)


@router.post("/api/educator/pacing-drift-reports", status_code=status.HTTP_201_CREATED)
def create_pacing_drift_report(
    payload: DriftReportInput,
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
) -> dict[str, object]:
    _no_store(response)
    actor = _actor(request, session)
    AuthenticationService(session).require_roles(actor, UserRole.EDUCATOR)
    tenant = _tenant(session, actor)
    assigned_stream = session.scalar(
        select(ClassStreamLedger.id).where(
            ClassStreamLedger.tenant_id == tenant.id,
            ClassStreamLedger.educator_id == actor.id,
            ClassStreamLedger.stream_package == payload.class_identifier,
            ClassStreamLedger.is_active.is_(True),
        )
    )
    if assigned_stream is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This class is not assigned to the signed-in educator.",
        )

    local_now = local_clock()
    slot_end = datetime.combine(
        payload.period_date,
        payload.period_end_time,
        JHB_TIME_ZONE,
    )
    if slot_end > local_now:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pacing drift can only be recorded after the scheduled period ends.",
        )
    if payload.period_date > local_now.date():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pacing drift cannot be recorded for a future date.",
        )

    existing = session.scalar(
        select(ClassroomPacingDriftReport).where(
            ClassroomPacingDriftReport.tenant_id == tenant.id,
            ClassroomPacingDriftReport.drift_key == payload.drift_key,
        )
    )
    if existing is not None:
        if existing.educator_id != actor.id:
            raise HTTPException(status_code=409, detail="This pacing drift was already reported.")
        existing.reason = payload.reason
        session.commit()
        session.refresh(existing)
        return {"id": existing.id, "drift_key": existing.drift_key, "saved": True}

    report = ClassroomPacingDriftReport(
        tenant_id=tenant.id,
        educator_id=actor.id,
        drift_key=payload.drift_key,
        period_slot_key=payload.period_slot_key,
        period_number=payload.period_number,
        period_label=payload.period_label,
        period_date=payload.period_date,
        period_start_time=_time_text(payload.period_start_time),
        period_end_time=_time_text(payload.period_end_time),
        class_identifier=payload.class_identifier,
        grade=payload.grade,
        subject=payload.subject,
        lesson_title=payload.lesson_title,
        reason=payload.reason,
        started_at=payload.started_at,
        completed_at=payload.completed_at,
    )
    session.add(report)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(ClassroomPacingDriftReport).where(
                ClassroomPacingDriftReport.tenant_id == tenant.id,
                ClassroomPacingDriftReport.drift_key == payload.drift_key,
            )
        )
        if existing is None:
            raise
        if existing.educator_id != actor.id:
            raise HTTPException(status_code=409, detail="This pacing drift was already reported.")
        existing.reason = payload.reason
        session.commit()
        report = existing
    else:
        session.refresh(report)
    return {"id": report.id, "drift_key": report.drift_key, "saved": True}


@router.get("/api/admin/pacing-drift-reports", status_code=status.HTTP_200_OK)
def list_pacing_drift_reports(
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
) -> dict[str, object]:
    _no_store(response)
    actor = _actor(request, session)
    AuthenticationService(session).require_roles(actor, UserRole.PRINCIPAL)
    tenant = _tenant(session, actor)
    rows = session.execute(
        select(ClassroomPacingDriftReport, User.display_name)
        .join(User, User.id == ClassroomPacingDriftReport.educator_id)
        .where(ClassroomPacingDriftReport.tenant_id == tenant.id)
        .order_by(
            ClassroomPacingDriftReport.period_date.desc(),
            ClassroomPacingDriftReport.period_end_time.desc(),
            ClassroomPacingDriftReport.id.desc(),
        )
        .limit(200)
    ).all()
    return {
        "reports": [_drift_payload(report, name) for report, name in rows],
    }