"""SQLAlchemy models for users, authentication, and role-scoped access."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import re
import logging
import secrets
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Date,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    select,
    String,
    Text,
    UniqueConstraint,
    Enum as SAEnum,
    inspect as sa_inspect,
    text,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, synonym, object_session, Session
from sqlalchemy.dialects.mysql import MEDIUMTEXT


AUTO_INCREMENT_ID = BigInteger().with_variant(Integer, "sqlite")

DEFAULT_UI_LAYOUT_DECKS: dict[str, object] = {
    "theme": "fieldnotes",
    "primary_color": "#B8563E",
    "secondary_color": "#F0E3D8",
    "font_family": "DM Sans",
    "crest_url": None,
}


def utc_now() -> datetime:
    return datetime.utcnow()


def generate_tenant_account_identifier() -> str:
    """Generate a human-shareable tenant identifier, never a credential."""
    return f"Fn{secrets.randbelow(10_000_000):07d}"


class Base(DeclarativeBase):
    pass


class SchoolSubject(Base):
    """A DBE subject available within one South African schooling phase."""

    __tablename__ = "school_subjects"
    __table_args__ = (
        UniqueConstraint(
            "phase",
            "name",
            name="uq_school_subjects_phase_name",
        ),
        Index("ix_school_subjects_phase_order", "phase", "display_order"),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    phase: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )


class SchoolSubjectCatalog(Base):
    """A tenant's active subject package for one grade year and class stream."""

    __tablename__ = "school_subject_catalog"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "subject_id",
            "grade_year",
            "stream_package",
            name="uq_school_subject_catalog_assignment",
        ),
        Index(
            "ix_school_subject_catalog_tenant_grade_stream",
            "tenant_id",
            "grade_year",
            "stream_package",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("school_subjects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    grade_year: Mapped[str] = mapped_column(String(48), nullable=False)
    stream_package: Mapped[str] = mapped_column(String(96), nullable=False)
    subject_note: Mapped[str] = mapped_column(
        String(180),
        nullable=False,
        default="Assigned learning pathway",
        server_default="Assigned learning pathway",
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class ClassStreamLedger(Base):
    """A tenant's class stream package used to assemble scholar enrollments."""

    __tablename__ = "class_stream_ledgers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "grade_year",
            "stream_package",
            name="uq_class_stream_ledgers_tenant_grade_stream",
        ),
        Index(
            "ix_class_stream_ledgers_tenant_active",
            "tenant_id",
            "is_active",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    grade_year: Mapped[str] = mapped_column(String(48), nullable=False)
    stream_package: Mapped[str] = mapped_column(String(96), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    educator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class StudentAcademicEnrollment(Base):
    """The active academic stream assignment for one scholar in one school year."""

    __tablename__ = "student_academic_enrollments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "scholar_id",
            "academic_year",
            name="uq_student_academic_enrollments_year",
        ),
        Index(
            "ix_student_academic_enrollments_tenant_scholar_status",
            "tenant_id",
            "scholar_id",
            "enrollment_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    scholar_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    class_stream_id: Mapped[int] = mapped_column(
        ForeignKey("class_stream_ledgers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    academic_year: Mapped[str] = mapped_column(
        String(16), nullable=False, default="CURRENT", server_default="CURRENT"
    )
    enrollment_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="ACTIVE", server_default="ACTIVE"
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class TeacherProfile(Base):
    """A contact-safe educator portfolio shown inside the staff workspace."""

    __tablename__ = "teacher_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="SET NULL"),
        nullable=True,
    )
    photo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    university_qualifications: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    career_background: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    specialisms: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    secure_chat_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class CurriculumDeck(Base):
    """A reusable curriculum keyword deck that may be assigned to many schools."""

    __tablename__ = "curriculum_decks"

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    rules: Mapped[list[PedagogicalStandardRule]] = relationship(
        back_populates="deck",
        cascade="all, delete-orphan",
        order_by="PedagogicalStandardRule.id",
    )
    school_tenants: Mapped[list[SchoolTenant]] = relationship(
        back_populates="curriculum_deck"
    )


class PedagogicalStandardRule(Base):
    """One trait-category regex rule belonging to a curriculum deck."""

    __tablename__ = "pedagogical_standard_rules"
    __table_args__ = (
        CheckConstraint(
            "trait_category IN "
            "('analytical_logic', 'spatial_visual', 'kinesthetic_mastery')",
            name="ck_pedagogical_standard_rule_trait_category",
        ),
        UniqueConstraint(
            "deck_id",
            "trait_category",
            name="uq_pedagogical_standard_rule_deck_category",
        ),
        Index(
            "ix_pedagogical_standard_rules_deck_category",
            "deck_id",
            "trait_category",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    deck_id: Mapped[int] = mapped_column(
        ForeignKey("curriculum_decks.id", ondelete="CASCADE"),
        nullable=False,
    )
    trait_category: Mapped[str] = mapped_column(String(48), nullable=False)
    keywords_regex_string: Mapped[str] = mapped_column(Text, nullable=False)

    deck: Mapped[CurriculumDeck] = relationship(back_populates="rules")


class SchoolTenant(Base):
    """A school registration record with an optional assigned curriculum deck."""

    __tablename__ = "school_tenants"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_school_tenants_slug"),
        UniqueConstraint(
            "account_identifier", name="uq_school_tenants_account_identifier"
        ),
        UniqueConstraint(
            "district_registry_code",
            name="uq_school_tenants_district_registry_code",
        ),
        Index("ix_school_tenants_curriculum_deck", "curriculum_deck_id"),
        CheckConstraint(
            "tier_status IN ('STANDARD', 'EXECUTIVE', 'PREMIUM')",
            name="ck_school_tenants_tier_status",
        ),
        CheckConstraint(
            "ui_layout_deck IN "
            "('CLASSIC_ACADEMICS', 'NEUMORPHIC_MODERN', 'CYBER_ACADEMY')",
            name="ck_school_tenants_ui_layout_deck",
        ),
        CheckConstraint(
            "billing_status IN ('ACTIVE', 'PAST_DUE')",
            name="ck_school_tenants_billing_status",
        ),
        CheckConstraint(
            "school_phase IN ('PRIMARY', 'HIGH_SCHOOL')",
            name="ck_school_tenants_school_phase",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    public_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, default=lambda: str(uuid4())
    )
    account_identifier: Mapped[str] = mapped_column(
        String(9),
        unique=True,
        nullable=False,
        default=generate_tenant_account_identifier,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    district_registry_code: Mapped[str | None] = mapped_column(String(80))
    location: Mapped[str | None] = mapped_column(String(240))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    plan: Mapped[str] = mapped_column(
        String(48), nullable=False, default="Standard Growth"
    )
    tier_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="STANDARD", server_default="STANDARD"
    )
    ui_layout_deck: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="CLASSIC_ACADEMICS",
        server_default="CLASSIC_ACADEMICS",
    )
    contract_terms_text: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    billing_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE", server_default="ACTIVE"
    )
    school_phase: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PRIMARY", server_default="PRIMARY"
    )
    ui_layout_decks: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: dict(DEFAULT_UI_LAYOUT_DECKS),
    )
    curriculum_deck_id: Mapped[int | None] = mapped_column(
        ForeignKey("curriculum_decks.id", ondelete="SET NULL"),
        nullable=True,
    )

    curriculum_deck: Mapped[CurriculumDeck | None] = relationship(
        back_populates="school_tenants"
    )
    memberships: Mapped[list[SchoolTenantMembership]] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )
    teacher_notes: Mapped[list[TeacherNote]] = relationship(
        back_populates="school_tenant"
    )
    scholar_traits: Mapped[list[TenantScholarTrait]] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )
    scholar_telemetry_profiles: Mapped[list[TenantScholarTelemetryProfile]] = (
        relationship(
            back_populates="school_tenant",
            cascade="all, delete-orphan",
        )
    )
    pedagogical_probes: Mapped[list[PedagogicalProbe]] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )
    simulation_tag_activations: Mapped[list[SimulationTagActivation]] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )
    interaction_telemetry: Mapped[list[InteractionTelemetry]] = relationship(
        back_populates="school_tenant"
    )
    finance_ledger_entries: Mapped[list["TenantFinanceLedger"]] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )
    digital_assets: Mapped[list["DigitalAssetVault"]] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )
    hod_pacing_trackers: Mapped[list["HodPacingTracker"]] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )
    elibrary_catalog_entries: Mapped[list["ELibraryCatalog"]] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )
    classroom_station_registry: Mapped[list["ClassroomStationRegistry"]] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )
    parent_subscription_ledgers: Mapped[list["ParentSubscriptionLedger"]] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )
    daily_attendance_registers: Mapped[list["DailyAttendanceRegister"]] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )
    parent_report_acknowledgments: Mapped[
        list["ParentReportAcknowledgment"]
    ] = relationship(
        back_populates="school_tenant",
        cascade="all, delete-orphan",
    )


class ClassroomHandshake(Base):
    """A short-lived, single-use teacher-to-scholar classroom assertion."""

    __tablename__ = "classroom_handshakes"
    __table_args__ = (
        Index("ix_classroom_handshakes_scholar_active", "scholar_id", "redeemed_at"),
        Index("ix_classroom_handshakes_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(AUTO_INCREMENT_ID, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    educator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scholar_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    active_topic_key: Mapped[str | None] = mapped_column(String(180), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(160), nullable=True)
    graphical_signature: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    review_topics: Mapped[list[dict[str, str]] | None] = mapped_column(
        JSON, nullable=True, default=list
    )
    period_slot_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    period_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response_token_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class LessonProgress(Base):
    """Tenant-scoped per-scholar lesson progress synchronized from educators."""

    __tablename__ = "lesson_progress"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "scholar_id",
            "lesson_key",
            name="uq_lesson_progress_tenant_scholar_lesson",
        ),
        Index("ix_lesson_progress_scholar_period", "tenant_id", "scholar_id", "period_date"),
    )

    id: Mapped[int] = mapped_column(AUTO_INCREMENT_ID, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    scholar_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    class_stream_id: Mapped[int] = mapped_column(
        ForeignKey("class_stream_ledgers.id", ondelete="RESTRICT"), nullable=False
    )
    lesson_key: Mapped[str] = mapped_column(String(180), nullable=False)
    active_topic_key: Mapped[str] = mapped_column(String(180), nullable=False)
    subject: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    subject_label: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    period_date: Mapped[date] = mapped_column(Date, nullable=False)
    initiated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ClassroomPeriodSchedule(Base):
    """The principal-managed, tenant-wide timetable period map."""

    __tablename__ = "classroom_period_schedules"

    id: Mapped[int] = mapped_column(AUTO_INCREMENT_ID, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    slots: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class ClassroomPacingDriftReport(Base):
    """A teacher-reported missed classroom period, visible to tenant principals."""

    __tablename__ = "classroom_pacing_drift_reports"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "drift_key",
            name="uq_classroom_pacing_drift_tenant_key",
        ),
        CheckConstraint(
            "reason IN ('LOAD_SHEDDING', 'UNEXPECTED_ASSEMBLY', "
            "'REMEDIAL_EXTENSION', 'EXTRA_MURAL_OVERRUN')",
            name="ck_classroom_pacing_drift_reason",
        ),
        Index(
            "ix_classroom_pacing_drift_tenant_date",
            "tenant_id",
            "period_date",
        ),
    )

    id: Mapped[int] = mapped_column(AUTO_INCREMENT_ID, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    educator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    drift_key: Mapped[str] = mapped_column(String(180), nullable=False)
    period_slot_key: Mapped[str] = mapped_column(String(32), nullable=False)
    period_number: Mapped[int] = mapped_column(Integer, nullable=False)
    period_label: Mapped[str] = mapped_column(String(80), nullable=False)
    period_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_start_time: Mapped[str] = mapped_column(String(5), nullable=False)
    period_end_time: Mapped[str] = mapped_column(String(5), nullable=False)
    class_identifier: Mapped[str] = mapped_column(
        String(80), nullable=False, default=""
    )
    grade: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    lesson_title: Mapped[str] = mapped_column(String(180), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class TenantFinanceLedger(Base):
    """An append-only financial record for a provisioned school tenant."""

    __tablename__ = "tenant_finance_ledgers"
    __table_args__ = (
        CheckConstraint(
            "amount_due >= 0",
            name="ck_tenant_finance_ledgers_amount_due",
        ),
        CheckConstraint(
            "amount_paid >= 0 AND amount_paid <= amount_due",
            name="ck_tenant_finance_ledgers_amount_paid",
        ),
        Index(
            "ix_tenant_finance_ledgers_tenant_date",
            "tenant_id",
            "date_issued",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_reference: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    amount_due: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )
    date_issued: Mapped[date] = mapped_column(
        Date, nullable=False, default=lambda: utc_now().date()
    )
    line_item_description: Mapped[str] = mapped_column(Text, nullable=False)

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="finance_ledger_entries"
    )


class SystemIssueTicket(Base):
    """A tenant-linked operational issue submitted for support review."""

    __tablename__ = "system_issue_tickets"
    __table_args__ = (
        Index(
            "ix_system_issue_tickets_timestamp_status",
            "timestamp",
            "ticket_status",
        ),
        Index(
            "ix_system_issue_tickets_tenant_timestamp",
            "tenant_id",
            "timestamp",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    user_role: Mapped[str] = mapped_column(String(40), nullable=False)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    diagnostic_metric: Mapped[str] = mapped_column(Text, nullable=False)
    ticket_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="Review",
        server_default="Review",
    )
    school_tenant: Mapped[SchoolTenant] = relationship()


class LearnerPrivacyRequest(Base):
    """A private tenant-bound record for a verified family privacy request."""

    __tablename__ = "learner_privacy_requests"
    __table_args__ = (
        CheckConstraint(
            "request_scope IN ('ACCESS', 'CORRECTION', 'OBJECTION', 'DELETION')",
            name="ck_learner_privacy_requests_scope",
        ),
        CheckConstraint(
            "request_status IN ('RECEIVED', 'IN_REVIEW', 'NEEDS_INFORMATION', "
            "'COMPLETED', 'DECLINED')",
            name="ck_learner_privacy_requests_status",
        ),
        Index(
            "ix_learner_privacy_requests_tenant_status_created",
            "tenant_id",
            "request_status",
            "created_at",
        ),
        Index(
            "ix_learner_privacy_requests_requester_created",
            "requester_user_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="RESTRICT"), nullable=False
    )
    requester_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    scholar_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    request_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    request_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="RECEIVED", server_default="RECEIVED"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class UserRole(str, Enum):
    MASTER_ADMIN = "MASTER_ADMIN"
    PRINCIPAL = "PRINCIPAL"
    GRADE_HOD = "GRADE_HOD"
    EDUCATOR = "EDUCATOR"
    SCHOLAR = "SCHOLAR"
    PARENT = "PARENT"


class ResourceAccessTier(str, Enum):
    """Presentation-safe access tiers for the curriculum catalog."""

    STANDARD_UNBLOCKED = "STANDARD_UNBLOCKED"


class SouthAfricanGradePhase(str, Enum):
    FOUNDATION_PHASE = "FOUNDATION_PHASE"
    INTERMEDIATE_PHASE = "INTERMEDIATE_PHASE"
    SENIOR_PHASE = "SENIOR_PHASE"
    FET_PHASE = "FET_PHASE"


class PlaygroundGameType(str, Enum):
    LEKGOTLA_DIALOGUE = "LEKGOTLA_DIALOGUE"
    PATTERN_ARCHITECT = "PATTERN_ARCHITECT"
    CANVAS_DRAFTER = "CANVAS_DRAFTER"
    KINETIC_TRACER = "KINETIC_TRACER"
    RHYTHM_SYMPHONY = "RHYTHM_SYMPHONY"
    ECO_TAXONOMIST = "ECO_TAXONOMIST"


class VastUtilityToolType(str, Enum):
    STORYBOARD = "STORYBOARD"
    MICRO_JOURNAL = "MICRO_JOURNAL"
    SPEED_READER = "SPEED_READER"
    EQUATION_BUILDER = "EQUATION_BUILDER"
    METRICS_TICKER = "METRICS_TICKER"
    SEQUENCE_SOLVER = "SEQUENCE_SOLVER"
    MIND_MAPPER = "MIND_MAPPER"
    GEOMETRY_SANDBOX = "GEOMETRY_SANDBOX"
    THEME_CUSTOMIZER = "THEME_CUSTOMIZER"
    BREATHING_ENGINE = "BREATHING_ENGINE"
    TYPING_VAULT = "TYPING_VAULT"
    GESTURE_TRACER = "GESTURE_TRACER"
    SOUNDSCAPE_MIXER = "SOUNDSCAPE_MIXER"
    CADENCE_TAPPER = "CADENCE_TAPPER"
    PEER_HINT = "PEER_HINT"
    CONFIDENCE_METER = "CONFIDENCE_METER"
    MISTAKE_VAULT = "MISTAKE_VAULT"
    FILE_TAXONOMIST = "FILE_TAXONOMIST"
    E_BOOK_SPRINT = "E_BOOK_SPRINT"
    E_WORKBOOK_CANVAS = "E_WORKBOOK_CANVAS"


class CareerSimulationType(str, Enum):
    VENTURE_CAPITAL = "VENTURE_CAPITAL"
    SARS_EFILING = "SARS_EFILING"


class SouthAfricanLanguage(str, Enum):
    ENGLISH = "ENGLISH"
    AFRIKAANS = "AFRIKAANS"
    ISIZULU = "ISIZULU"
    ISIXHOSA = "ISIXHOSA"
    SEPEDI = "SEPEDI"
    SETSWANA = "SETSWANA"
    SESOTHO = "SESOTHO"
    XITSONGA = "XITSONGA"
    SISWATI = "SISWATI"
    TCOVENDA = "TCOVENDA"
    ISINDEBELE = "ISINDEBELE"
    SASL = "SASL"
    BRAILLE = "BRAILLE"


class GamifiedEmpathyBadgeType(str, Enum):
    GRIT_PERSEVERANCE = "GRIT_PERSEVERANCE"
    COLLABORATION = "COLLABORATION"
    DEEP_FOCUS = "DEEP_FOCUS"
    PATTERN_MASTERY = "PATTERN_MASTERY"


def role_values(role_enum: type[UserRole]) -> list[str]:
    return [role.value for role in role_enum]


def grade_phase_for_grade_tier(grade_tier: str) -> SouthAfricanGradePhase:
    """Map a South African grade label to its CAPS phase."""
    normalized = grade_tier.strip().lower()
    if normalized in {"r", "grade r", "gr r", "reception"}:
        return SouthAfricanGradePhase.FOUNDATION_PHASE
    match = re.search(r"\b(?:grade|gr)\s*(\d{1,2})\b", normalized)
    if match is None:
        return SouthAfricanGradePhase.FOUNDATION_PHASE
    grade = int(match.group(1))
    if grade <= 3:
        return SouthAfricanGradePhase.FOUNDATION_PHASE
    if grade <= 6:
        return SouthAfricanGradePhase.INTERMEDIATE_PHASE
    if grade <= 9:
        return SouthAfricanGradePhase.SENIOR_PHASE
    if grade <= 12:
        return SouthAfricanGradePhase.FET_PHASE
    return SouthAfricanGradePhase.FOUNDATION_PHASE


def grade_year_for_grade_tier(grade_tier: str) -> int | None:
    """Return the numeric South African grade, with Grade R represented by zero."""
    normalized = grade_tier.strip().lower()
    if normalized in {"r", "grade r", "gr r", "reception"}:
        return 0
    match = re.search(r"\b(?:grade|gr)\s*(\d{1,2})\b", normalized)
    if match is None:
        return None
    grade = int(match.group(1))
    return grade if 0 <= grade <= 12 else None


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    public_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, default=lambda: str(uuid4())
    )
    profile_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(
            UserRole,
            name="user_role",
            values_callable=role_values,
            native_enum=True,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    auth_credential: Mapped[AuthCredential | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    auth_sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    master_admin_profile: Mapped[MasterAdminProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    principal_profile: Mapped[PrincipalProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    grade_hod_profile: Mapped[GradeHodProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    educator_profile: Mapped[EducatorProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    scholar_profile: Mapped[ScholarProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    parent_profile: Mapped[ParentProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    school_tenant_memberships: Mapped[list[SchoolTenantMembership]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    scholar_rental_ledger_entries: Mapped[list["ScholarRentalLedger"]] = relationship(
        back_populates="scholar",
        cascade="all, delete-orphan",
    )
    assigned_classroom_stations: Mapped[list["ClassroomStationRegistry"]] = relationship(
        back_populates="assigned_scholar",
        foreign_keys="ClassroomStationRegistry.assigned_scholar_id",
    )
    parent_subscription_ledgers: Mapped[list["ParentSubscriptionLedger"]] = relationship(
        back_populates="parent_user",
        foreign_keys="ParentSubscriptionLedger.parent_user_id",
    )
    scholar_subscription_ledgers: Mapped[list["ParentSubscriptionLedger"]] = relationship(
        back_populates="scholar_user",
        foreign_keys="ParentSubscriptionLedger.scholar_user_id",
    )


class DigitalAssetVault(Base):
    """Tenant-scoped educational content available to the E-Library."""

    __tablename__ = "digital_asset_vault"
    __table_args__ = (
        Index(
            "ix_digital_asset_vault_tenant_phase",
            "tenant_id",
            "target_phase",
        ),
        Index(
            "ix_digital_asset_vault_tenant_genre",
            "tenant_id",
            "genre_classification",
        ),
        UniqueConstraint(
            "tenant_id",
            "title",
            name="uq_digital_asset_vault_tenant_title",
        ),
    )

    asset_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    target_phase: Mapped[str] = mapped_column(
        String(50),
        index=True,
        nullable=False,
    )
    genre_classification: Mapped[str] = mapped_column(
        String(100),
        index=True,
        nullable=False,
    )
    curriculum_term: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="Public Domain Reference",
        server_default="Public Domain Reference",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_image_vector: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_document_text: Mapped[str] = mapped_column(Text, nullable=False)
    access_tier: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ResourceAccessTier.STANDARD_UNBLOCKED.value,
        server_default=ResourceAccessTier.STANDARD_UNBLOCKED.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    license_identifier: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="digital_assets",
    )
    scholar_rentals: Mapped[list["ScholarRentalLedger"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
    )


class HodPacingTracker(Base):
    """Tenant and grade-scoped pacing telemetry for the HOD read model."""

    __tablename__ = "hod_pacing_trackers"
    __table_args__ = (
        CheckConstraint(
            "planned_days >= 0 AND actual_days >= 0",
            name="ck_hod_pacing_trackers_non_negative_days",
        ),
        CheckConstraint(
            "classroom_friction_metric >= 0 AND classroom_friction_metric <= 100",
            name="ck_hod_pacing_trackers_friction_metric",
        ),
        Index(
            "ix_hod_pacing_trackers_tenant_grade_subject",
            "tenant_id",
            "grade_level",
            "subject_name",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    grade_level: Mapped[str] = mapped_column(String(48), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(160), nullable=False)
    planned_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    days_deviation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classroom_friction_metric: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    friction_reason_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    source_label: Mapped[str] = mapped_column(
        String(120), nullable=False, default="HOD pacing telemetry"
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="hod_pacing_trackers"
    )


class ELibraryCatalog(Base):
    """Tenant-scoped book preview metadata separate from full asset content."""

    __tablename__ = "elibrary_catalog"
    __table_args__ = (
        CheckConstraint(
            "rating >= 0 AND rating <= 5",
            name="ck_elibrary_catalog_rating",
        ),
        CheckConstraint(
            "access_state IN ('OPEN_ACCESS', 'PREVIEW_ONLY', 'MISSION_LOCKED')",
            name="ck_elibrary_catalog_access_state",
        ),
        Index(
            "ix_elibrary_catalog_tenant_grade_active",
            "tenant_id",
            "grade_level",
            "is_active",
        ),
        UniqueConstraint(
            "tenant_id",
            "grade_level",
            "title",
            name="uq_elibrary_catalog_tenant_grade_title",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    grade_level: Mapped[str] = mapped_column(String(48), nullable=False)
    phase_scope: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preview_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    cover_image_url: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
    )
    rights_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="AUDITED", server_default="AUDITED"
    )
    access_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PREVIEW_ONLY", server_default="PREVIEW_ONLY"
    )
    access_state_label: Mapped[str] = mapped_column(
        String(80), nullable=False, default="Preview available", server_default="Preview available"
    )
    digital_access_token_label: Mapped[str] = mapped_column(
        String(120), nullable=False, default="Digital access · verified"
    )
    rating: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rating_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="elibrary_catalog_entries"
    )


class ScholarRentalLedger(Base):
    """Tenant-independent audit trail for a scholar's asset rental pairing."""

    __tablename__ = "scholar_rental_ledger"
    __table_args__ = (
        Index(
            "ix_scholar_rental_ledger_scholar_active",
            "scholar_id",
            "is_active",
        ),
        Index(
            "ix_scholar_rental_ledger_asset",
            "asset_id",
        ),
    )

    rental_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    scholar_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("digital_asset_vault.asset_id", ondelete="CASCADE"),
        nullable=False,
    )
    pairing_token_used: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    network_origin_ip: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )
    rented_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    scholar: Mapped[User] = relationship(
        back_populates="scholar_rental_ledger_entries",
    )
    asset: Mapped[DigitalAssetVault] = relationship(
        back_populates="scholar_rentals",
    )


class ClassroomStationRegistry(Base):
    """Tenant-scoped classroom terminal state for secure teacher interception."""

    __tablename__ = "classroom_station_registry"
    __table_args__ = (
        CheckConstraint(
            "session_status IN ('ACTIVE', 'INACTIVE_OPEN', 'IDLE')",
            name="ck_classroom_station_registry_session_status",
        ),
        Index(
            "ix_classroom_station_registry_tenant_status",
            "tenant_id",
            "session_status",
        ),
    )

    station_uuid: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    station_label: Mapped[str] = mapped_column(String(64), nullable=False)
    assigned_scholar_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_application_module: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
    )
    last_activity_timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    session_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="classroom_station_registry",
    )
    assigned_scholar: Mapped[User | None] = relationship(
        back_populates="assigned_classroom_stations",
        foreign_keys=[assigned_scholar_id],
    )


class ClassroomStationAuditLog(Base):
    """Immutable tenant-scoped record of principal station configuration changes."""

    __tablename__ = "classroom_station_audit_log"
    __table_args__ = (
        Index(
            "ix_classroom_station_audit_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "ix_classroom_station_audit_tenant_station",
            "tenant_id",
            "station_uuid",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    station_uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    station_label_before: Mapped[str | None] = mapped_column(String(64))
    station_label_after: Mapped[str | None] = mapped_column(String(64))
    assigned_scholar_id_before: Mapped[int | None] = mapped_column(AUTO_INCREMENT_ID)
    assigned_scholar_id_after: Mapped[int | None] = mapped_column(AUTO_INCREMENT_ID)
    assigned_scholar_name_before: Mapped[str | None] = mapped_column(String(160))
    assigned_scholar_name_after: Mapped[str | None] = mapped_column(String(160))
    is_active_before: Mapped[bool | None] = mapped_column(Boolean)
    is_active_after: Mapped[bool | None] = mapped_column(Boolean)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )


class ParentSubscriptionLedger(Base):
    """Parent-authorized leisure rental and token allowance ledger."""

    __tablename__ = "parent_subscription_ledgers"
    __table_args__ = (
        CheckConstraint(
            "subscription_tier IN ('FREE_CURRICULUM', 'PREMIUM_ELITE_VAULT')",
            name="ck_parent_subscription_ledgers_tier",
        ),
        CheckConstraint(
            "request_status IN ('PENDING', 'UNLOCKED', 'DECLINED')",
            name="ck_parent_subscription_ledgers_request_status",
        ),
        CheckConstraint(
            "available_tokens >= 0 AND available_tokens <= 2",
            name="ck_parent_subscription_ledgers_available_tokens",
        ),
        Index(
            "ix_parent_subscription_ledgers_parent_status",
            "parent_user_id",
            "request_status",
        ),
        Index(
            "ix_parent_subscription_ledgers_tenant_parent",
            "tenant_id",
            "parent_user_id",
        ),
    )

    ledger_uuid: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    scholar_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    premium_leisure_book_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("digital_asset_vault.asset_id", ondelete="SET NULL"),
        nullable=True,
    )
    premium_leisure_book_title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    premium_leisure_book_author: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    subscription_tier: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="FREE_CURRICULUM",
        server_default="FREE_CURRICULUM",
    )
    available_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
        server_default="2",
    )
    request_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paired_external_device_uuid: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="parent_subscription_ledgers",
    )
    parent_user: Mapped[User] = relationship(
        back_populates="parent_subscription_ledgers",
        foreign_keys=[parent_user_id],
    )
    scholar_user: Mapped[User] = relationship(
        back_populates="scholar_subscription_ledgers",
        foreign_keys=[scholar_user_id],
    )
    premium_leisure_book_asset: Mapped[DigitalAssetVault | None] = relationship(
        foreign_keys=[premium_leisure_book_asset_id],
    )


class ParentBehavioralMissionLedger(Base):
    """A persisted parent-led real-world learning mission and its evidence."""

    __tablename__ = "parent_behavioral_mission_ledgers"
    __table_args__ = (
        CheckConstraint(
            "mission_status IN ('AVAILABLE', 'PENDING', 'COMPLETED')",
            name="ck_parent_behavioral_missions_status",
        ),
        Index(
            "ix_parent_behavioral_missions_tenant_parent_status",
            "tenant_id",
            "parent_user_id",
            "mission_status",
        ),
        Index(
            "ix_parent_behavioral_missions_tenant_scholar",
            "tenant_id",
            "scholar_user_id",
        ),
    )

    mission_uuid: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    scholar_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    recommendation_text: Mapped[str] = mapped_column(Text, nullable=False)
    mission_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="AVAILABLE",
        server_default="AVAILABLE",
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    qualitative_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    alternative_activity: Mapped[str | None] = mapped_column(Text, nullable=True)
    termly_report_index: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSON,
        nullable=True,
        default=list,
    )
    report_indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    school_tenant: Mapped[SchoolTenant] = relationship()
    parent_user: Mapped[User] = relationship(foreign_keys=[parent_user_id])
    scholar_user: Mapped[User] = relationship(foreign_keys=[scholar_user_id])


def generate_parent_registration_token() -> str:
    """Generate an alphanumeric one-time registration key for a parent slot."""
    import secrets
    import string

    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(32))


class ScholarParentTokenVault(Base):
    """Two server-issued parent registration slots for one scholar account."""

    __tablename__ = "scholar_parent_token_vaults"
    __table_args__ = (
        UniqueConstraint("scholar_id", name="uq_scholar_parent_vault_scholar"),
        Index("ix_scholar_parent_vault_scholar", "scholar_id"),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    scholar_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    parent_token_a: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        default=generate_parent_registration_token,
    )
    parent_token_b: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        default=generate_parent_registration_token,
    )
    parent_a_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    parent_b_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )


class SchoolTenantMembership(Base):
    """An active user's server-authoritative membership in a school tenant."""

    __tablename__ = "school_tenant_memberships"
    __table_args__ = (
        Index(
            "ix_school_tenant_memberships_user_active",
            "user_id",
            "is_active",
        ),
    )

    school_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="memberships"
    )
    user: Mapped[User] = relationship(back_populates="school_tenant_memberships")


class SchoolStaffInvitation(Base):
    """One-time, tenant-bound authorization to register school staff."""

    __tablename__ = "school_staff_invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_school_staff_invitations_token_hash"),
        Index(
            "ix_school_staff_invitations_tenant_status",
            "school_tenant_id",
            "status",
        ),
        Index(
            "ix_school_staff_invitations_tenant_email",
            "school_tenant_id",
            "email",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    school_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ISSUED", server_default="ISSUED"
    )
    issued_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class SchoolStaffInvitationAudit(Base):
    """Append-only record of school staff invitation lifecycle events."""

    __tablename__ = "school_staff_invitation_audit"
    __table_args__ = (
        Index(
            "ix_school_staff_invitation_audit_tenant_time",
            "school_tenant_id",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    invitation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("school_staff_invitations.id", ondelete="CASCADE"),
        nullable=False,
    )
    school_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class TenantStorageConfiguration(Base):
    """Private object-storage and byte-quota settings for one tenant."""

    __tablename__ = "tenant_storage_configurations"
    __table_args__ = (
        CheckConstraint(
            "storage_provider IN ('MANAGED', 'BYOB')",
            name="ck_tenant_storage_configurations_provider",
        ),
        CheckConstraint(
            "storage_quota_bytes > 0",
            name="ck_tenant_storage_configurations_quota",
        ),
    )

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    storage_provider: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_quota_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bucket_name: Mapped[str | None] = mapped_column(String(255))
    endpoint_url: Mapped[str | None] = mapped_column(String(512))
    region_name: Mapped[str | None] = mapped_column(String(64))
    encrypted_access_key: Mapped[str | None] = mapped_column(Text)
    encrypted_secret_key: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class TenantStorageCleanupJob(Base):
    """A durable retry for a copied object whose remote deletion failed."""

    __tablename__ = "tenant_storage_cleanup_jobs"
    __table_args__ = (
        Index(
            "ix_tenant_storage_cleanup_jobs_tenant_created",
            "tenant_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    bucket_name: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(768), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    last_attempt_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class TenantReportConfiguration(Base):
    """Master-admin import rules and tenant-owned report labels."""

    __tablename__ = "tenant_report_configurations"

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    supported_formats: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=lambda: ["CSV", "XLSX", "JSON"]
    )
    header_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="AUTO", server_default="AUTO"
    )
    date_format: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ISO", server_default="ISO"
    )
    term_label: Mapped[str] = mapped_column(
        String(40), nullable=False, default="Academic term", server_default="Academic term"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class TenantReportRuleHistory(Base):
    """Immutable tenant-scoped snapshots of report-import rules."""

    __tablename__ = "tenant_report_rule_history"
    __table_args__ = (
        Index(
            "ix_tenant_report_rule_history_tenant_changed",
            "tenant_id",
            "changed_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    supported_formats: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    header_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    date_format: Mapped[str] = mapped_column(String(16), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    changed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class StoredAsset(Base):
    """Tenant-scoped file metadata and upload reservation ledger."""

    __tablename__ = "stored_assets"
    __table_args__ = (
        CheckConstraint(
            "file_size_bytes > 0",
            name="ck_stored_assets_file_size",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'COMPLETE', 'EXPIRED')",
            name="ck_stored_assets_status",
        ),
        Index("ix_stored_assets_tenant_status", "tenant_id", "status"),
        Index("ix_stored_assets_pending_expiry", "tenant_id", "status", "expires_at"),
        Index(
            "ix_stored_assets_report_processing",
            "report_status",
            "report_started_at",
        ),
        UniqueConstraint("object_key", name="uq_stored_assets_object_key"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    grade_category: Mapped[str] = mapped_column(String(32), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    permanent_storage_url: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    object_key: Mapped[str] = mapped_column(String(768), nullable=False)
    bucket_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    report_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="NOT_PROCESSED",
        server_default="NOT_PROCESSED",
    )
    report_progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    report_error: Mapped[str | None] = mapped_column(Text)
    report_records: Mapped[
        dict[str, object] | list[dict[str, object]] | None
    ] = mapped_column(JSON)
    report_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    report_finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    report_job_token: Mapped[str | None] = mapped_column(String(36))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class TenantReportExport(Base):
    """Durable status and private-storage pointer for a filtered report export."""

    __tablename__ = "tenant_report_exports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'PROCESSING', 'COMPLETE', 'FAILED')",
            name="ck_tenant_report_exports_status",
        ),
        Index(
            "ix_tenant_report_exports_tenant_status",
            "tenant_id",
            "status",
        ),
        UniqueConstraint(
            "tenant_id",
            "request_fingerprint",
            name="uq_tenant_report_exports_request",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_asset_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    filters: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    term_label: Mapped[str] = mapped_column(String(40), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="QUEUED", server_default="QUEUED"
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error: Mapped[str | None] = mapped_column(Text)
    object_key: Mapped[str | None] = mapped_column(String(768))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    job_token: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class TenantStorageBackup(Base):
    """One daily, tenant-isolated restore point for managed object storage."""

    __tablename__ = "tenant_storage_backups"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_tenant_storage_backups_status",
        ),
        UniqueConstraint(
            "tenant_id",
            "backup_date",
            name="uq_tenant_storage_backups_daily",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_tenant_storage_backups_tenant_id",
        ),
        Index("ix_tenant_storage_backups_tenant_date", "tenant_id", "backup_date"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    backup_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="RUNNING", server_default="RUNNING"
    )
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    failure_message: Mapped[str | None] = mapped_column(String(255))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class TenantStorageBackupItem(Base):
    """A verified copy of one file included in a tenant backup snapshot."""

    __tablename__ = "tenant_storage_backup_items"
    __table_args__ = (
        UniqueConstraint(
            "backup_id",
            "source_asset_id",
            name="uq_tenant_storage_backup_items_source",
        ),
        Index(
            "ix_tenant_storage_backup_items_tenant_backup",
            "tenant_id",
            "backup_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "backup_id"],
            ["tenant_storage_backups.tenant_id", "tenant_storage_backups.id"],
            ondelete="CASCADE",
            name="fk_tenant_storage_backup_items_tenant_backup",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    backup_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
    )
    source_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    grade_category: Mapped[str] = mapped_column(String(32), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    backup_bucket_name: Mapped[str] = mapped_column(String(255), nullable=False)
    backup_object_key: Mapped[str] = mapped_column(String(768), nullable=False)
    restored_asset_id: Mapped[str | None] = mapped_column(String(36))


class RosterAccountActivationHistory(Base):
    """Append-only, tenant-scoped account and membership activation history."""

    __tablename__ = "roster_account_activation_history"
    __table_args__ = (
        Index(
            "ix_roster_account_activation_history_tenant_time",
            "school_tenant_id",
            "changed_at",
            "id",
        ),
        Index(
            "ix_roster_account_activation_history_user",
            "user_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    school_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    change_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_display_name: Mapped[str | None] = mapped_column(String(160))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    user: Mapped[User | None] = relationship(foreign_keys=[user_id])


def roster_activation_history_schema_ready(session: Session) -> bool:
    """Check the approved history table without activating it via broad migrations."""
    cached = session.info.get("roster_activation_history_schema_ready")
    if cached is not None:
        return bool(cached)
    ready = sa_inspect(session.connection()).has_table(
        RosterAccountActivationHistory.__tablename__
    )
    session.info["roster_activation_history_schema_ready"] = ready
    return ready


def _roster_history_entry(
    *,
    tenant_id: int,
    user: User,
    scope: str,
    is_active: bool,
    session: Session,
) -> RosterAccountActivationHistory:
    return RosterAccountActivationHistory(
        school_tenant_id=tenant_id,
        user_id=user.id,
        display_name=user.display_name,
        role=user.role.value,
        change_scope=scope,
        is_active=is_active,
        actor_user_id=session.info.get("roster_activation_actor_id"),
        actor_display_name=session.info.get("roster_activation_actor_name"),
        user=user,
    )


def _membership_history_user(
    session: Session,
    membership: SchoolTenantMembership,
) -> User | None:
    user = membership.user
    if user is not None:
        return user
    if membership.user_id is not None:
        user = session.get(User, membership.user_id)
        if user is not None:
            return user
    return next(
        (
            item
            for item in session.new
            if isinstance(item, User) and item.id == membership.user_id
        ),
        None,
    )


@event.listens_for(Session, "before_flush")
def _record_roster_activation_changes(session, _flush_context, _instances) -> None:
    new_memberships = [
        item
        for item in session.new
        if isinstance(item, SchoolTenantMembership)
    ]
    dirty_memberships = [
        item
        for item in session.dirty
        if isinstance(item, SchoolTenantMembership)
        and sa_inspect(item).attrs.is_active.history.has_changes()
    ]
    dirty_users = [
        item
        for item in session.dirty
        if isinstance(item, User)
        and sa_inspect(item).attrs.is_active.history.has_changes()
        and item.role in {UserRole.EDUCATOR, UserRole.SCHOLAR}
    ]
    if not (new_memberships or dirty_memberships or dirty_users):
        return
    if session.info.get("recording_roster_activation_history"):
        return
    if not roster_activation_history_schema_ready(session):
        logging.getLogger(__name__).warning(
            "Roster activation history capture awaits approved schema activation."
        )
        return

    session.info["recording_roster_activation_history"] = True
    try:
        for membership in new_memberships:
            user = _membership_history_user(session, membership)
            if (
                user is None
                or user.role not in {UserRole.EDUCATOR, UserRole.SCHOLAR}
            ):
                continue
            session.add(
                _roster_history_entry(
                    tenant_id=membership.school_tenant_id,
                    user=user,
                    scope="TENANT_MEMBERSHIP",
                    is_active=getattr(membership, "is_active", True) is not False,
                    session=session,
                )
            )

        for membership in dirty_memberships:
            user = _membership_history_user(session, membership)
            if (
                user is None
                or user.role not in {UserRole.EDUCATOR, UserRole.SCHOLAR}
            ):
                continue
            session.add(
                _roster_history_entry(
                    tenant_id=membership.school_tenant_id,
                    user=user,
                    scope="TENANT_MEMBERSHIP",
                    is_active=bool(membership.is_active),
                    session=session,
                )
            )

        for user in dirty_users:
            with session.no_autoflush:
                memberships = session.scalars(
                    select(SchoolTenantMembership).where(
                        SchoolTenantMembership.user_id == user.id
                    )
                ).all()
            memberships.extend(
                membership
                for membership in new_memberships
                if membership.user is user
                and membership not in memberships
            )
            for membership in memberships:
                session.add(
                    _roster_history_entry(
                        tenant_id=membership.school_tenant_id,
                        user=user,
                        scope="USER_ACCOUNT",
                        is_active=bool(user.is_active),
                        session=session,
                    )
                )
    finally:
        session.info.pop("recording_roster_activation_history", None)


class SchoolPersonnelRoleLink(Base):
    """Tenant-scoped personnel hierarchy and delegated admin permissions."""

    __tablename__ = "school_personnel_role_links"
    __table_args__ = (
        UniqueConstraint(
            "school_tenant_id",
            "user_id",
            name="uq_school_personnel_role_link_tenant_user",
        ),
        Index(
            "ix_school_personnel_role_links_tenant_assistant",
            "school_tenant_id",
            "is_assistant_admin",
        ),
        Index(
            "ix_school_personnel_role_links_reporting_hod",
            "reporting_hod_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    school_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_assistant_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    assigned_by_principal_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reporting_hod_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class CalendarBroadcastEvent(Base):
    """A tenant-scoped calendar event broadcast to one role audience."""

    __tablename__ = "calendar_broadcast_events"
    __table_args__ = (
        CheckConstraint(
            "audience_tier IN ('TEACHER', 'PARENT', 'SCHOLAR', 'ALL')",
            name="ck_calendar_broadcast_events_audience_tier",
        ),
        CheckConstraint(
            "reactive_type_parameter IS NULL OR reactive_type_parameter IN "
            "('RSVP_ATTENDING_COUNT', 'DIGITAL_CONSENT_SIGNATURE', "
            "'DOCUMENT_UPLOAD_REQUEST', 'READ_ACKNOWLEDGE_TICK')",
            name="ck_calendar_broadcast_events_reactive_type_parameter",
        ),
        CheckConstraint(
            "(is_reactive = FALSE AND reactive_type_parameter IS NULL) OR "
            "(is_reactive = TRUE AND reactive_type_parameter IS NOT NULL)",
            name="ck_calendar_broadcast_events_reactive_pair",
        ),
        Index(
            "ix_calendar_broadcast_events_tenant_audience_active",
            "tenant_id",
            "audience_tier",
            "is_active",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    creator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    event_details: Mapped[str] = mapped_column(Text, nullable=False)
    audience_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    is_reactive: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    reactive_type_parameter: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )
    # Compatibility alias for clients and seed data written before the
    # reactive_type_parameter name was made part of the public contract.
    reactive_action_type = synonym("reactive_type_parameter")
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class CalendarBroadcastAuditLog(Base):
    """Append-only creation audit record for a calendar broadcast."""

    __tablename__ = "calendar_broadcast_audit_logs"
    __table_args__ = (
        Index(
            "ix_calendar_broadcast_audit_logs_event_occurred",
            "event_id",
            "occurred_at",
        ),
        Index(
            "ix_calendar_broadcast_audit_logs_tenant_occurred",
            "tenant_id",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("calendar_broadcast_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class CalendarBroadcastDeliveryLog(Base):
    """Persisted gateway result for one tenant-scoped mobile alert dispatch."""

    __tablename__ = "calendar_broadcast_delivery_logs"
    __table_args__ = (
        Index(
            "ix_calendar_broadcast_delivery_logs_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "ix_calendar_broadcast_delivery_logs_event",
            "event_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_audience_tier: Mapped[str] = mapped_column(
        String(16), nullable=False
    )
    dispatched_recipient_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    undelivered_recipient_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    gateway_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="LOCAL_SIMULATION",
        server_default="LOCAL_SIMULATION",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class UndeliveredSmsVault(Base):
    """Append-only record of recipients that cannot receive an outbound message."""

    __tablename__ = "undelivered_sms_vault"
    __table_args__ = (
        Index(
            "ix_undelivered_sms_vault_tenant_event",
            "tenant_id",
            "event_id",
            "created_at",
        ),
        Index(
            "ix_undelivered_sms_vault_recipient",
            "recipient_user_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_audience_tier: Mapped[str] = mapped_column(
        String(16), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(96), nullable=False)
    message_body_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class IncidentCrisisLog(Base):
    """Append-only ledger entry for a verified emergency broadcast."""

    __tablename__ = "incident_crisis_logs"
    __table_args__ = (
        Index(
            "ix_incident_crisis_logs_initiator_timestamp",
            "initiator_id",
            "timestamp",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    initiator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    incident_type: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload_broadcasted: Mapped[str] = mapped_column(Text, nullable=False)


class BroadcastUserResponse(Base):
    """One user response record for one calendar broadcast event."""

    __tablename__ = "broadcast_user_responses"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "user_id",
            name="uq_broadcast_user_responses_event_user",
        ),
        CheckConstraint(
            "response_status IN ('Pending', 'Confirmed', 'Declined')",
            name="ck_broadcast_user_responses_status",
        ),
        Index(
            "ix_broadcast_user_responses_user_updated",
            "user_id",
            "updated_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("calendar_broadcast_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    response_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="Pending",
        server_default="Pending",
    )
    payload_response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class AcademicYearPlanner(Base):
    """Tenant-scoped year roadmap generated from a curriculum standard."""

    __tablename__ = "academic_year_planners"
    __table_args__ = (
        CheckConstraint(
            "curriculum_standard IN ('CAPS', 'IEB', 'Oxford', 'Cambridge')",
            name="ck_academic_year_planners_curriculum_standard",
        ),
        Index(
            "ix_academic_year_planners_tenant_grade",
            "tenant_id",
            "grade_level",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    grade_level: Mapped[str] = mapped_column(String(48), nullable=False)
    curriculum_standard: Mapped[str] = mapped_column(String(16), nullable=False)
    syllabus_map_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )
    is_approved_by_hod: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class CurriculumPacingLog(Base):
    """A teacher's tenant-scoped chapter pacing status log."""

    __tablename__ = "curriculum_pacing_logs"
    __table_args__ = (
        CheckConstraint(
            "pacing_status IN ('ACTIVE', 'PAUSED')",
            name="ck_curriculum_pacing_logs_status",
        ),
        CheckConstraint(
            "pause_reason_code IS NULL OR pause_reason_code IN "
            "('LEARNER_RETENTIVENESS_FRICTION', "
            "'DISRUPTIVE_ENVIRONMENTAL_FACTOR', "
            "'INSUFFICIENT_FACILITY_RESOURCES', "
            "'INSUFFICIENT_RESOURCES')",
            name="ck_curriculum_pacing_logs_pause_reason",
        ),
        CheckConstraint(
            "days_delayed >= 0",
            name="ck_curriculum_pacing_logs_days_delayed",
        ),
        CheckConstraint(
            "(pacing_status = 'ACTIVE' AND pause_reason_code IS NULL) OR "
            "(pacing_status = 'PAUSED' AND pause_reason_code IS NOT NULL)",
            name="ck_curriculum_pacing_logs_status_reason_pair",
        ),
        CheckConstraint(
            "action_state IS NULL OR action_state IN ('START', 'PAUSE')",
            name="ck_curriculum_pacing_logs_action_state",
        ),
        Index(
            "ix_curriculum_pacing_logs_tenant_grade_status",
            "tenant_id",
            "grade_tier",
            "pacing_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    planner_id: Mapped[int | None] = mapped_column(
        ForeignKey("academic_year_planners.id", ondelete="SET NULL"),
        nullable=True,
    )
    chapter_identifier: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )
    action_state: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
    )
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("school_subjects.id", ondelete="RESTRICT"),
        nullable=True,
    )
    grade_tier: Mapped[str] = mapped_column(String(48), nullable=False)
    chapter_title: Mapped[str] = mapped_column(String(180), nullable=False)
    pacing_status: Mapped[str] = mapped_column(String(8), nullable=False)
    pause_reason_code: Mapped[str | None] = mapped_column(
        String(48),
        nullable=True,
    )
    days_delayed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    logged_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )


class TenantCanvasBlueprint(Base):
    """A persisted low-code canvas layout for one tenant viewport."""

    __tablename__ = "tenant_canvas_blueprints"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "dashboard_viewport",
            name="uq_tenant_canvas_blueprints_tenant_viewport",
        ),
        Index(
            "ix_tenant_canvas_blueprints_tenant_viewport_updated",
            "tenant_id",
            "dashboard_viewport",
            "last_updated_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    dashboard_viewport: Mapped[str] = mapped_column(
        String(96),
        nullable=False,
    )
    serialized_json_layout: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class LowCodeUserProjectSnapshot(Base):
    """An independently saved low-code project owned by one school tenant."""

    __tablename__ = "low_code_user_project_snapshots"
    __table_args__ = (
        Index(
            "ix_low_code_project_snapshots_tenant_created",
            "tenant_id",
            "created_timestamp",
        ),
    )

    project_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    project_title: Mapped[str] = mapped_column(String(120), nullable=False)
    selected_base_layout_type: Mapped[str] = mapped_column(String(48), nullable=False)
    canvas_blueprint_data: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )
    created_timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Keep the public project contract's camelCase names available to callers.
    projectId = synonym("project_id")
    projectTitle = synonym("project_title")
    selectedBaseLayoutType = synonym("selected_base_layout_type")
    canvasBlueprintData = synonym("canvas_blueprint_data")
    createdTimestamp = synonym("created_timestamp")


class FieldnotesResearchProject(Base):
    """Platform-owned Research Studio work, kept outside school tenant data."""

    __tablename__ = "fieldnotes_research_projects"
    __table_args__ = (
        Index(
            "ix_fieldnotes_research_projects_owner_updated",
            "owner_user_id",
            "updated_at",
        ),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    research_track: Mapped[str] = mapped_column(String(24), nullable=False)
    research_question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="DRAFT", server_default="DRAFT"
    )
    workspace_data: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class FieldnotesResearchSource(Base):
    """A manual URL, note, or private uploaded file attached to a platform project."""

    __tablename__ = "fieldnotes_research_sources"
    __table_args__ = (
        Index(
            "ix_fieldnotes_research_sources_owner_project",
            "owner_user_id",
            "project_id",
        ),
    )

    source_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("fieldnotes_research_projects.project_id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    file_name: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(160))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    object_key: Mapped[str | None] = mapped_column(String(768))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    analysis_data: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class LowCodeWebsiteSnapshot(Base):
    """A school-owned or platform-owned semantic low-code website draft."""

    __tablename__ = "low_code_website_snapshots"
    __table_args__ = (
        Index(
            "ix_low_code_website_snapshots_tenant_created",
            "tenant_id",
            "created_timestamp",
        ),
    )

    site_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    site_title: Mapped[str] = mapped_column(String(120), nullable=False)
    selected_web_template_type: Mapped[str] = mapped_column(
        String(48),
        nullable=False,
    )
    semantic_blueprint_data: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )
    created_timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=True,
    )

    siteId = synonym("site_id")
    siteTitle = synonym("site_title")
    selectedWebTemplateType = synonym("selected_web_template_type")
    semanticBlueprintData = synonym("semantic_blueprint_data")
    createdTimestamp = synonym("created_timestamp")


class WebsiteMediaAsset(Base):
    """Private, reusable image metadata scoped to one website owner."""

    __tablename__ = "website_media_assets"
    __table_args__ = (
        Index(
            "ix_website_media_assets_owner_created",
            "owner_tenant_id",
            "created_at",
        ),
    )

    asset_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    owner_tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    alt_text: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    image_data: Mapped[str] = mapped_column(
        Text().with_variant(MEDIUMTEXT(), "mysql"),
        nullable=False,
    )
    uploaded_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class LowCodeWorkspaceFolder(Base):
    """A tenant-owned folder for saved Canvas projects or website drafts."""

    __tablename__ = "low_code_workspace_folders"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "collection",
            "folder_key",
            name="uq_low_code_workspace_folders_scope_key",
        ),
        CheckConstraint(
            "collection IN ('canvas-projects', 'semantic-websites')",
            name="ck_low_code_workspace_folders_collection",
        ),
        Index(
            "ix_low_code_workspace_folders_tenant_collection",
            "tenant_id",
            "collection",
        ),
    )

    folder_id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    collection: Mapped[str] = mapped_column(String(32), nullable=False)
    folder_name: Mapped[str] = mapped_column(String(80), nullable=False)
    folder_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )


class LowCodeWorkspaceFolderItem(Base):
    """Maps one tenant-owned saved item to one folder without altering its snapshot."""

    __tablename__ = "low_code_workspace_folder_items"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "collection",
            "item_id",
            name="uq_low_code_workspace_folder_items_item",
        ),
        CheckConstraint(
            "collection IN ('canvas-projects', 'semantic-websites')",
            name="ck_low_code_workspace_folder_items_collection",
        ),
        Index(
            "ix_low_code_workspace_folder_items_folder",
            "tenant_id",
            "collection",
            "folder_id",
        ),
    )

    assignment_id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    collection: Mapped[str] = mapped_column(String(32), nullable=False)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    folder_id: Mapped[int] = mapped_column(
        ForeignKey("low_code_workspace_folders.folder_id", ondelete="CASCADE"),
        nullable=False,
    )


class PublishedWebsiteRevision(Base):
    """Immutable school-owned or platform-owned published website content."""

    __tablename__ = "published_revisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "site_id",
            "revision_number",
            name="uq_published_revisions_tenant_site_revision",
        ),
        UniqueConstraint(
            "hostname",
            name="uq_published_revisions_hostname",
        ),
        Index(
            "ix_published_revisions_tenant_site_published",
            "tenant_id",
            "site_id",
            "published_at",
        ),
        CheckConstraint(
            "revision_number > 0",
            name="ck_published_revisions_positive_revision",
        ),
        CheckConstraint(
            "color_preset IN ('original', 'walnut', 'obsidian')",
            name="ck_published_revisions_color_preset",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID,
        primary_key=True,
        autoincrement=True,
    )
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    site_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(253), nullable=True)
    site_title: Mapped[str] = mapped_column(String(120), nullable=False)
    selected_web_template_type: Mapped[str] = mapped_column(
        String(48),
        nullable=False,
    )
    semantic_blueprint_data: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )
    color_preset: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="original",
        server_default="original",
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )


class TenantWebsiteDomain(Base):
    """A custom website hostname claimed by a school or the platform."""

    __tablename__ = "tenant_website_domains"
    __table_args__ = (
        UniqueConstraint("hostname", name="uq_tenant_website_domains_hostname"),
        Index(
            "ix_tenant_website_domains_tenant_verified",
            "tenant_id",
            "verified_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    dns_verification_token: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    verification_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    is_domain_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    verification_expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retired_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retired_by_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    previously_live_site_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    previously_live_site_title: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    previously_live_revision_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )


class WebsiteEnquiry(Base):
    """Private enquiry submitted through one active published website."""

    __tablename__ = "website_enquiries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'handled')",
            name="ck_website_enquiries_status",
        ),
        Index(
            "ix_website_enquiries_owner_status_submitted",
            "tenant_id",
            "status",
            "submitted_at",
        ),
        Index(
            "ix_website_enquiries_owner_site_submitted",
            "tenant_id",
            "site_id",
            "submitted_at",
        ),
        Index(
            "ix_website_enquiries_spam_window",
            "hostname",
            "ip_hash",
            "submitted_at",
        ),
        Index("ix_website_enquiries_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    site_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    sender_name: Mapped[str] = mapped_column(String(120), nullable=False)
    sender_email: Mapped[str] = mapped_column(String(254), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    consent_text: Mapped[str] = mapped_column(String(240), nullable=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="new",
        server_default="new",
    )
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class VerifiedLowCodeCatalogTemplate(Base):
    """An anonymized, master-reviewed low-code template with no tenant owner."""

    __tablename__ = "verified_low_code_catalog_templates"
    __table_args__ = (
        CheckConstraint(
            "layout_type IN ('DASHBOARD_CANVAS', 'SEMANTIC_SITE')",
            name="ck_verified_low_code_catalog_layout_type",
        ),
        Index(
            "ix_verified_low_code_catalog_layout_type",
            "layout_type",
        ),
    )

    template_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    template_title: Mapped[str] = mapped_column(String(120), nullable=False)
    layout_type: Mapped[str] = mapped_column(String(32), nullable=False)
    blueprint_config_data: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )
    last_verified_timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    templateId = synonym("template_id")
    templateTitle = synonym("template_title")
    layoutType = synonym("layout_type")
    blueprintConfigData = synonym("blueprint_config_data")
    lastVerifiedTimestamp = synonym("last_verified_timestamp")


class VerifiedLowCodeCatalogPromotionAudit(Base):
    """Checksum-only audit trail for an explicitly selected production promotion."""

    __tablename__ = "verified_low_code_catalog_promotion_audits"
    __table_args__ = (
        Index(
            "ix_verified_catalog_promotion_tenant_time",
            "target_tenant_id",
            "promoted_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "verified_low_code_catalog_templates.template_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    target_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    layout_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_key: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_content_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    deployment_pass_id: Mapped[str] = mapped_column(String(64), nullable=False)
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )


class VerifiedLowCodeCatalogPromotionOutbox(Base):
    """Production-only WebSocket refresh event written with each promotion."""

    __tablename__ = "verified_low_code_catalog_promotion_outbox"
    __table_args__ = (
        Index(
            "ix_verified_catalog_outbox_tenant_view_id",
            "tenant_id",
            "dashboard_viewport",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    dashboard_viewport: Mapped[str] = mapped_column(String(96), nullable=False)
    message_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )


class AuthCredential(Base):
    __tablename__ = "auth_credentials"

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped[User] = relationship(back_populates="auth_credential")


class ScholarSignInFailure(Base):
    """Minimal event record for a rejected scholar sign-in."""

    __tablename__ = "scholar_sign_in_failures"
    __table_args__ = (
        Index(
            "ix_scholar_sign_in_failures_failed_at",
            "failed_at",
        ),
        Index(
            "ix_scholar_sign_in_failures_scholar_failed_at",
            "scholar_user_id",
            "failed_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    scholar_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    failed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    action_context: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("(JSON_OBJECT())")
    )

    user: Mapped[User] = relationship(back_populates="auth_sessions")


class MasterAdminProfile(Base):
    __tablename__ = "master_admin_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    backend_access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    system_log_access: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    override_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    user: Mapped[User] = relationship(back_populates="master_admin_profile")


class MasterAdminAuthenticator(Base):
    """Encrypted per-user authenticator state; the historic name is schema-compatible."""

    __tablename__ = "master_admin_authenticators"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    last_verified_step: Mapped[int | None] = mapped_column(BigInteger)
    recovery_code_hashes: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    enabled_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class MasterAdminBootstrapState(Base):
    """Single-use registration state for the first owner-authorized admin setup."""

    __tablename__ = "master_admin_bootstrap_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_master_admin_bootstrap_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    claimed_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    pending_email: Mapped[str | None] = mapped_column(String(255))
    pending_display_name: Mapped[str | None] = mapped_column(String(160))
    pending_password_hash: Mapped[str | None] = mapped_column(String(255))
    pending_secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    pending_challenge_hash: Mapped[str | None] = mapped_column(String(128))
    pending_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)


class MasterAdminTenantStorageAccessSession(Base):
    """Short-lived, tenant-bound access granted for audited Master Admin support."""

    __tablename__ = "master_admin_tenant_storage_access_sessions"
    __table_args__ = (
        CheckConstraint(
            "access_mode IN ('READ_ONLY', 'MAINTENANCE')",
            name="ck_master_admin_tenant_storage_access_mode",
        ),
        Index(
            "ix_master_admin_tenant_storage_access_admin_tenant",
            "admin_user_id",
            "tenant_id",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    admin_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    access_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)


class MasterAdminTenantStorageAccessEvent(Base):
    """Append-only record of Master Admin access to a tenant's client storage UI."""

    __tablename__ = "master_admin_tenant_storage_access_events"
    __table_args__ = (
        Index(
            "ix_master_admin_tenant_storage_access_events_tenant_time",
            "tenant_id",
            "created_at",
        ),
        Index(
            "ix_master_admin_tenant_storage_access_events_session_time",
            "session_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey(
            "master_admin_tenant_storage_access_sessions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    admin_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    access_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    http_method: Mapped[str | None] = mapped_column(String(10))
    request_path: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class PrincipalProfile(Base):
    __tablename__ = "principal_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    school_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    pacing_settings: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    notice_publish_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    user: Mapped[User] = relationship(back_populates="principal_profile")


class GradeHodProfile(Base):
    __tablename__ = "grade_hod_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    grade_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    department_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    grade_data_visibility: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    user: Mapped[User] = relationship(back_populates="grade_hod_profile")


class EducatorProfile(Base):
    __tablename__ = "educator_profiles"
    __table_args__ = (
        CheckConstraint(
            "active_classroom_state IN "
            "('STANDARD_INSTRUCTION', 'AUTOMATED_SIMULATION_MODE')",
            name="ck_educator_profiles_active_classroom_state",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    grade_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    class_identifier: Mapped[str] = mapped_column(String(80), nullable=False)
    attendance_register_access: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    lesson_notes_access: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    active_classroom_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="STANDARD_INSTRUCTION",
        server_default="STANDARD_INSTRUCTION",
    )

    user: Mapped[User] = relationship(back_populates="educator_profile")


class ScholarProfile(Base):
    __tablename__ = "scholar_profiles"
    __table_args__ = (
        CheckConstraint(
            "analytical_logic_score >= 0 AND analytical_logic_score <= 100",
            name="ck_scholar_profile_analytical_logic_score",
        ),
        CheckConstraint(
            "spatial_visual_score >= 0 AND spatial_visual_score <= 100",
            name="ck_scholar_profile_spatial_visual_score",
        ),
        CheckConstraint(
            "kinesthetic_mastery_score >= 0 AND kinesthetic_mastery_score <= 100",
            name="ck_scholar_profile_kinesthetic_mastery_score",
        ),
        CheckConstraint(
            "fidget_index >= 0 AND fidget_index <= 100",
            name="ck_scholar_profile_fidget_index",
        ),
        CheckConstraint(
            "conative_grit >= 0 AND conative_grit <= 100",
            name="ck_scholar_profile_conative_grit",
        ),
        CheckConstraint(
            "grade_year IS NULL OR (grade_year >= 0 AND grade_year <= 12)",
            name="ck_scholar_profile_grade_year",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    grade_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    grade_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_grade_phase: Mapped[SouthAfricanGradePhase] = mapped_column(
        SAEnum(
            SouthAfricanGradePhase,
            name="south_african_grade_phase",
            values_callable=role_values,
            native_enum=True,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
        default=SouthAfricanGradePhase.FOUNDATION_PHASE,
        server_default=SouthAfricanGradePhase.FOUNDATION_PHASE.value,
    )
    viewport_tracking_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    calculator_telemetry_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    conative_grit_tracking_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    latest_conative_grit_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2)
    )
    analytical_logic_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    spatial_visual_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    kinesthetic_mastery_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    fidget_index: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    conative_grit: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )

    def __init__(self, **kwargs: Any) -> None:
        if "current_grade_phase" not in kwargs:
            kwargs["current_grade_phase"] = grade_phase_for_grade_tier(
                kwargs.get("grade_tier", "")
            )
        if "grade_year" not in kwargs:
            kwargs["grade_year"] = grade_year_for_grade_tier(
                kwargs.get("grade_tier", "")
            )
        super().__init__(**kwargs)

    user: Mapped[User] = relationship(back_populates="scholar_profile")


class ParentProfile(Base):
    __tablename__ = "parent_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    household_identifier: Mapped[str] = mapped_column(String(80), nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    child_radar_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    user: Mapped[User] = relationship(back_populates="parent_profile")


class ParentScholarLink(Base):
    __tablename__ = "parent_scholar_links"
    __table_args__ = (
        CheckConstraint(
            "parent_user_id <> scholar_user_id",
            name="ck_parent_scholar_distinct_users",
        ),
        Index("ix_parent_scholar_links_scholar", "scholar_user_id"),
    )

    parent_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    scholar_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    relationship_label: Mapped[str] = mapped_column(String(40), nullable=False)
    radar_strength_map_access: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    re_signature_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class ParentRegisteredDevice(Base):
    """One server-verified browser registration for each parent account."""

    __tablename__ = "parent_registered_devices"
    __table_args__ = (
        UniqueConstraint("parent_user_id", name="uq_parent_registered_device_parent"),
        UniqueConstraint("device_id", name="uq_parent_registered_device_device"),
        UniqueConstraint("token_hash", name="uq_parent_registered_device_token"),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    parent_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(
        String(36), nullable=False, default=lambda: str(uuid4())
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class DailyAttendanceRegister(Base):
    """A tenant-scoped daily attendance decision for one scholar."""

    __tablename__ = "daily_attendance_registers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PRESENT', 'ABSENT', 'LATE')",
            name="ck_daily_attendance_registers_status",
        ),
        UniqueConstraint(
            "tenant_id",
            "educator_id",
            "scholar_id",
            "attendance_date",
            name="uq_daily_attendance_registers_entry",
        ),
        Index(
            "ix_daily_attendance_registers_tenant_date",
            "tenant_id",
            "attendance_date",
        ),
        Index(
            "ix_daily_attendance_registers_scholar_date",
            "scholar_id",
            "attendance_date",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    educator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    scholar_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="daily_attendance_registers"
    )


class ParentReportAcknowledgment(Base):
    """An archived, cryptographically verifiable parent report signature."""

    __tablename__ = "parent_report_acknowledgments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "parent_id",
            "student_id",
            "term",
            name="uq_parent_report_acknowledgments_report",
        ),
        Index(
            "ix_parent_report_acknowledgments_tenant_student",
            "tenant_id",
            "student_id",
        ),
        Index(
            "ix_parent_report_acknowledgments_parent_signed",
            "parent_id",
            "signed_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    term: Mapped[str] = mapped_column(String(48), nullable=False)
    signature_text: Mapped[str] = mapped_column(String(160), nullable=False)
    signature_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="parent_report_acknowledgments"
    )


class ParentMonthlyContractSignature(Base):
    """Immutable parent acknowledgment for one scholar and calendar month."""

    __tablename__ = "parent_monthly_contract_signatures"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "parent_id",
            "student_id",
            "signature_month",
            "signature_revision",
            name="uq_parent_monthly_contract_signature",
        ),
        Index(
            "ix_parent_monthly_contract_student_month",
            "tenant_id",
            "student_id",
            "signature_month",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    signature_month: Mapped[str] = mapped_column(String(7), nullable=False)
    signature_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    signature_text: Mapped[str] = mapped_column(String(160), nullable=False)
    signature_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    legal_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    signed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    is_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class PedagogicalProbeStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"


class PedagogicalProbe(Base):
    """A server-generated, tenant-scoped recommendation for one scholar."""

    __tablename__ = "pedagogical_probes"
    __table_args__ = (
        Index(
            "ix_pedagogical_probes_tenant_scholar_status",
            "school_tenant_id",
            "scholar_user_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    school_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    scholar_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    grade_band: Mapped[str] = mapped_column(String(40), nullable=False)
    recommendation_text: Mapped[str] = mapped_column(Text, nullable=False)
    simulation_tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    source_context: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    status: Mapped[PedagogicalProbeStatus] = mapped_column(
        SAEnum(
            PedagogicalProbeStatus,
            name="pedagogical_probe_status",
            values_callable=role_values,
            native_enum=True,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
        default=PedagogicalProbeStatus.PENDING,
    )
    accepted_by_educator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="pedagogical_probes"
    )


class SimulationTagActivation(Base):
    """A normalized record of server-owned tags activated by acceptance."""

    __tablename__ = "simulation_tag_activations"
    __table_args__ = (
        UniqueConstraint(
            "pedagogical_probe_id",
            "simulation_tag",
            name="uq_simulation_tag_activation_probe",
        ),
        Index(
            "ix_simulation_tag_activations_probe",
            "pedagogical_probe_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    school_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    scholar_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    pedagogical_probe_id: Mapped[int] = mapped_column(
        ForeignKey("pedagogical_probes.id", ondelete="CASCADE"), nullable=False
    )
    simulation_tag: Mapped[str] = mapped_column(String(80), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="simulation_tag_activations"
    )


class LessonObservation(Base):
    __tablename__ = "lesson_observations"
    __table_args__ = (
        Index(
            "ix_lesson_observations_scholar_observed",
            "scholar_user_id",
            "observed_at",
        ),
        Index(
            "ix_lesson_observations_educator_observed",
            "educator_user_id",
            "observed_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    educator_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scholar_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    behavior_tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class TeacherNote(Base):
    __tablename__ = "Teacher_Notes"
    __table_args__ = (
        Index(
            "ix_teacher_notes_student_created",
            "student_id",
            "created_at",
        ),
        Index(
            "ix_teacher_notes_educator_created",
            "educator_user_id",
            "created_at",
        ),
        Index(
            "ix_teacher_notes_tenant_student_created",
            "school_tenant_id",
            "student_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    educator_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    school_tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="RESTRICT"),
        nullable=True,
    )
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    behavior_tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )

    school_tenant: Mapped[SchoolTenant | None] = relationship(
        back_populates="teacher_notes"
    )


class ObservationCurriculumContext(Base):
    """Immutable scoring evidence; deck identity survives renames and deletion."""

    __tablename__ = "observation_curriculum_contexts"

    observation_id: Mapped[int] = mapped_column(
        ForeignKey("Teacher_Notes.id", ondelete="CASCADE"), primary_key=True
    )
    # Deliberately not a deck FK: historical evidence must outlive the deck.
    deck_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deck_name: Mapped[str] = mapped_column(String(120), nullable=False)
    category_hits: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)


@event.listens_for(TeacherNote, "after_insert")
def _persist_observation_curriculum_context(mapper, connection, observation):
    """Use the exact scorer output, on the note's own database transaction.

    Raw legacy imports do not run the scorer and must not acquire an invented
    historical curriculum. A scored submission cannot commit just its note if
    insertion of its context fails.
    """
    session = object_session(observation)
    if session is None:
        return
    prepared = session.info.get("observation_curriculum_contexts", {})
    context = prepared.pop(
        (observation.school_tenant_id, observation.note_text), None
    )
    if context is not None:
        from backend.telemetry.observation_context import curriculum_context_is_active

        # Preserve the legacy submission flow until separately approved schema
        # activation. Once active, a failed context insert still rolls back all
        # observation evidence; never silently skip an insert failure.
        if not curriculum_context_is_active(connection):
            logging.getLogger(__name__).warning(
                "Curriculum context capture awaits approved schema activation; "
                "observation saved using the legacy evidence format."
            )
            return
        connection.execute(
            ObservationCurriculumContext.__table__.insert().values(
                observation_id=observation.id, **context
            )
        )


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _clear_prepared_observation_context(session):
    # A reused session must not attribute later imports to a failed submission.
    session.info.pop("observation_curriculum_contexts", None)


class TelemetrySessionStatus(str, Enum):
    OPEN = "OPEN"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"


class ObservationTelemetrySession(Base):
    """A short-lived, tenant-bound telemetry capture assigned to an educator."""

    __tablename__ = "observation_telemetry_sessions"
    __table_args__ = (
        Index("ix_observation_telemetry_sessions_lookup",
              "school_tenant_id", "scholar_user_id", "status", "expires_at"),
        UniqueConstraint(
            "active_key",
            name="uq_observation_telemetry_session_active_key",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    educator_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scholar_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    school_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    active_key: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    status: Mapped[TelemetrySessionStatus] = mapped_column(
        SAEnum(TelemetrySessionStatus, name="telemetry_session_status",
               values_callable=role_values, native_enum=True,
               create_constraint=True, validate_strings=True),
        nullable=False, default=TelemetrySessionStatus.OPEN,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    canvas_clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    empty_duration_seconds: Mapped[float] = mapped_column(nullable=False, default=0)
    total_keystrokes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_duration_seconds: Mapped[float] = mapped_column(nullable=False, default=0)
    paste_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_observation_id: Mapped[int | None] = mapped_column(
        ForeignKey("Teacher_Notes.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    finalize_requested_at: Mapped[datetime | None] = mapped_column(DateTime)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime)


class TelemetrySessionBatchReceipt(Base):
    """Receipt making a client batch replay harmless."""

    __tablename__ = "telemetry_session_batch_receipts"
    __table_args__ = (
        UniqueConstraint("telemetry_session_id", "batch_id",
                         name="uq_telemetry_session_batch"),
    )
    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    telemetry_session_id: Mapped[str] = mapped_column(
        ForeignKey("observation_telemetry_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class OfflineWriteReceipt(Base):
    """Tenant- and actor-scoped receipt for replay-safe browser write retries."""

    __tablename__ = "offline_write_receipts"
    __table_args__ = (
        UniqueConstraint(
            "school_tenant_id",
            "actor_user_id",
            "operation",
            "idempotency_key",
            name="uq_offline_write_receipt_scope_key",
        ),
        Index(
            "ix_offline_write_receipts_created_at",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    school_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


# Short aliases retained for service and test callers that use the API term.
TelemetrySession = ObservationTelemetrySession
TelemetryBatchReceipt = TelemetrySessionBatchReceipt


class ScholarTrait(Base):
    __tablename__ = "Scholar_Traits"

    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kinesthetic_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    visual_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    analytical_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class TenantScholarTrait(Base):
    """A school-specific learning-style matrix for one scholar."""

    __tablename__ = "tenant_scholar_traits"
    __table_args__ = (
        Index(
            "ix_tenant_scholar_traits_student_updated",
            "student_id",
            "updated_at",
        ),
    )

    school_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    kinesthetic_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    visual_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    analytical_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="scholar_traits"
    )


class TenantScholarTelemetryProfile(Base):
    """A school-specific CAPS and activity snapshot for one scholar."""

    __tablename__ = "tenant_scholar_telemetry_profiles"
    __table_args__ = (
        Index(
            "ix_tenant_scholar_telemetry_profiles_student_updated",
            "student_id",
            "updated_at",
        ),
        CheckConstraint(
            "analytical_logic_score >= 0 AND analytical_logic_score <= 100",
            name="ck_tenant_scholar_telemetry_analytical_logic",
        ),
        CheckConstraint(
            "spatial_visual_score >= 0 AND spatial_visual_score <= 100",
            name="ck_tenant_scholar_telemetry_spatial_visual",
        ),
        CheckConstraint(
            "kinesthetic_mastery_score >= 0 AND kinesthetic_mastery_score <= 100",
            name="ck_tenant_scholar_telemetry_kinesthetic_mastery",
        ),
        CheckConstraint(
            "fidget_index >= 0 AND fidget_index <= 100",
            name="ck_tenant_scholar_telemetry_fidget_index",
        ),
        CheckConstraint(
            "conative_grit >= 0 AND conative_grit <= 100",
            name="ck_tenant_scholar_telemetry_conative_grit",
        ),
    )

    school_tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    analytical_logic_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    spatial_visual_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    kinesthetic_mastery_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    fidget_index: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    conative_grit: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    school_tenant: Mapped[SchoolTenant] = relationship(
        back_populates="scholar_telemetry_profiles"
    )


class InteractionTelemetry(Base):
    __tablename__ = "interaction_telemetry"
    __table_args__ = (
        Index(
            "ix_interaction_telemetry_tenant_user_occurred",
            "school_tenant_id",
            "user_id",
            "occurred_at",
        ),
        Index(
            "ix_interaction_telemetry_user_occurred",
            "user_id",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    school_tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="RESTRICT"),
        nullable=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_key: Mapped[str] = mapped_column(String(80), nullable=False)
    fidget_index: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("0")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    school_tenant: Mapped[SchoolTenant | None] = relationship(
        back_populates="interaction_telemetry"
    )


class OperationalMeetingEvent(Base):
    """A tenant-scoped operational meeting routed to multiple role audiences."""

    __tablename__ = "operational_meeting_events"
    __table_args__ = (
        CheckConstraint(
            "meeting_type IN ('IN_PERSON', 'VIRTUAL', 'BLENDED')",
            name="ck_operational_meeting_events_meeting_type",
        ),
        Index(
            "ix_operational_meeting_events_tenant_start",
            "tenant_id",
            "start_time",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    meeting_type: Mapped[str] = mapped_column(String(16), nullable=False)
    audience_composition_json: Mapped[str] = mapped_column(Text, nullable=False)


class ScholarSimulationFrictionFlag(Base):
    """A simulation friction signal attached to a scholar and active topic."""

    __tablename__ = "scholar_simulation_friction_flags"
    __table_args__ = (
        CheckConstraint(
            "flag_priority_type IN ('CONCEPT_BLOCK', 'TASK_STUCK', 'SPEED_AHEAD')",
            name="ck_scholar_simulation_friction_flags_priority",
        ),
        Index(
            "ix_scholar_simulation_friction_flags_scholar_timestamp",
            "scholar_id",
            "timestamp",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    scholar_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scholar_name: Mapped[str] = mapped_column(String(160), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    topic_identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    flag_priority_type: Mapped[str] = mapped_column(String(16), nullable=False)
    is_resolved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class ScholarBehavioralPatternLog(Base):
    """Raw interaction metrics from one completed scholar simulation block."""

    __tablename__ = "scholar_behavioral_pattern_logs"
    __table_args__ = (
        CheckConstraint(
            "session_duration_seconds >= 0",
            name="ck_scholar_behavioral_logs_session_duration",
        ),
        CheckConstraint(
            "total_interaction_clicks >= 0",
            name="ck_scholar_behavioral_logs_interaction_clicks",
        ),
        CheckConstraint(
            "friction_triage_count >= 0",
            name="ck_scholar_behavioral_logs_friction_triage",
        ),
        CheckConstraint(
            "text_read_seconds >= 0",
            name="ck_scholar_behavioral_logs_text_read_seconds",
        ),
        CheckConstraint(
            "widget_manipulations >= 0",
            name="ck_scholar_behavioral_logs_widget_manipulations",
        ),
        CheckConstraint(
            "diagram_clicks >= 0",
            name="ck_scholar_behavioral_logs_diagram_clicks",
        ),
        CheckConstraint(
            "auditory_toggles >= 0",
            name="ck_scholar_behavioral_logs_auditory_toggles",
        ),
        CheckConstraint(
            "reflection_logs >= 0",
            name="ck_scholar_behavioral_logs_reflection_logs",
        ),
        Index(
            "ix_scholar_behavioral_logs_tenant_scholar_timestamp",
            "tenant_id",
            "scholar_id",
            "timestamp",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scholar_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    total_interaction_clicks: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    friction_triage_count: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    text_read_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    widget_manipulations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    diagram_clicks: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    auditory_toggles: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reflection_logs: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class PlaygroundContentDeck(Base):
    """Age-bounded challenge content for Grades R through 9."""

    __tablename__ = "playground_content_decks"
    __table_args__ = (
        CheckConstraint(
            "grade_year >= 0 AND grade_year <= 9",
            name="ck_playground_content_decks_grade_year",
        ),
        CheckConstraint(
            "difficulty_multiplier > 0",
            name="ck_playground_content_decks_difficulty_multiplier",
        ),
        UniqueConstraint(
            "game_type",
            "grade_year",
            name="uq_playground_content_decks_game_grade",
        ),
        Index(
            "ix_playground_content_decks_game_grade",
            "game_type",
            "grade_year",
        ),
    )

    content_id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID,
        primary_key=True,
        autoincrement=True,
    )
    game_type: Mapped[PlaygroundGameType] = mapped_column(
        SAEnum(
            PlaygroundGameType,
            name="playground_content_game_type",
            values_callable=role_values,
            native_enum=True,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    grade_year: Mapped[int] = mapped_column(Integer, nullable=False)
    challenge_payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    difficulty_multiplier: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
    )


class ScholarCognitiveProfile(Base):
    """Tenant-scoped rolling cognitive profile derived from behavior logs."""

    __tablename__ = "scholar_cognitive_profiles"
    __table_args__ = (
        CheckConstraint(
            "cognitive_archetype IN "
            "('DEEP_ANALYTICAL', 'INTUITIVE_ACCELERATOR', "
            "'PERSISTENT_IMPLEMENTER')",
            name="ck_scholar_cognitive_profiles_archetype",
        ),
        CheckConstraint(
            "focus_endurance_score >= 0 AND focus_endurance_score <= 100",
            name="ck_scholar_cognitive_profiles_focus_endurance",
        ),
        CheckConstraint(
            "processing_velocity_score >= 0 AND processing_velocity_score <= 100",
            name="ck_scholar_cognitive_profiles_processing_velocity",
        ),
        CheckConstraint(
            "resilience_grit_score >= 0 AND resilience_grit_score <= 100",
            name="ck_scholar_cognitive_profiles_resilience_grit",
        ),
        CheckConstraint(
            "verbal_linguistic_score >= 0 AND verbal_linguistic_score <= 100",
            name="ck_scholar_cognitive_profiles_verbal_linguistic",
        ),
        CheckConstraint(
            "logical_mathematical_score >= 0 AND logical_mathematical_score <= 100",
            name="ck_scholar_cognitive_profiles_logical_mathematical",
        ),
        CheckConstraint(
            "visual_spatial_score >= 0 AND visual_spatial_score <= 100",
            name="ck_scholar_cognitive_profiles_visual_spatial",
        ),
        CheckConstraint(
            "bodily_kinesthetic_score >= 0 AND bodily_kinesthetic_score <= 100",
            name="ck_scholar_cognitive_profiles_bodily_kinesthetic",
        ),
        CheckConstraint(
            "musical_rhythmic_score >= 0 AND musical_rhythmic_score <= 100",
            name="ck_scholar_cognitive_profiles_musical_rhythmic",
        ),
        CheckConstraint(
            "interpersonal_score >= 0 AND interpersonal_score <= 100",
            name="ck_scholar_cognitive_profiles_interpersonal",
        ),
        CheckConstraint(
            "intrapersonal_score >= 0 AND intrapersonal_score <= 100",
            name="ck_scholar_cognitive_profiles_intrapersonal",
        ),
        CheckConstraint(
            "naturalist_score >= 0 AND naturalist_score <= 100",
            name="ck_scholar_cognitive_profiles_naturalist",
        ),
        CheckConstraint(
            "existential_score >= 0 AND existential_score <= 100",
            name="ck_scholar_cognitive_profiles_existential",
        ),
        UniqueConstraint(
            "tenant_id",
            "scholar_id",
            name="uq_scholar_cognitive_profiles_tenant_scholar",
        ),
        Index(
            "ix_scholar_cognitive_profiles_tenant_scholar",
            "tenant_id",
            "scholar_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scholar_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cognitive_archetype: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    focus_endurance_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    processing_velocity_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    resilience_grit_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    verbal_linguistic_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    logical_mathematical_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    visual_spatial_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    bodily_kinesthetic_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    musical_rhythmic_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    interpersonal_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    intrapersonal_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    naturalist_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    existential_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    playground_analytics_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        default=dict,
    )


class ScholarVastUtilityLog(Base):
    """Tenant-scoped engagement emitted by a VAST learning utility."""

    __tablename__ = "scholar_vast_utility_logs"
    __table_args__ = (
        CheckConstraint(
            "engagement_seconds > 0 AND engagement_seconds <= 86400",
            name="ck_scholar_vast_utility_logs_engagement_seconds",
        ),
        Index(
            "ix_scholar_vast_utility_logs_tenant_scholar_timestamp",
            "tenant_id",
            "scholar_id",
            "timestamp",
        ),
        Index(
            "ix_scholar_vast_utility_logs_tool_type",
            "tool_type",
        ),
        UniqueConstraint(
            "tenant_id",
            "scholar_id",
            "idempotency_key",
            name="uq_scholar_vast_utility_log_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scholar_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_type: Mapped[VastUtilityToolType] = mapped_column(
        SAEnum(
            VastUtilityToolType,
            name="vast_utility_tool_type",
            values_callable=role_values,
            native_enum=True,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    engagement_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workbook_strokes_json: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    workbook_text_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    workbook_follow_up_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class ScholarCareerSimulationScore(Base):
    """Latest tenant-scoped career simulation score for one scholar and deck."""

    __tablename__ = "scholar_career_simulation_scores"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "scholar_id",
            "simulation_type",
            name="uq_scholar_career_simulation_score",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_scholar_career_simulation_score_range",
        ),
        Index(
            "ix_scholar_career_simulation_scores_tenant_scholar",
            "tenant_id",
            "scholar_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scholar_id: Mapped[str] = mapped_column(String(64), nullable=False)
    simulation_type: Mapped[CareerSimulationType] = mapped_column(
        SAEnum(
            CareerSimulationType,
            name="career_simulation_type",
            values_callable=role_values,
            native_enum=True,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class ScholarCareerSimulationAttempt(Base):
    """One completed, tenant-scoped career simulation attempt."""

    __tablename__ = "scholar_career_simulation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "scholar_id",
            "simulation_type",
            "attempt_id",
            name="uq_scholar_career_simulation_attempt",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_scholar_career_simulation_attempt_score_range",
        ),
        Index(
            "ix_scholar_career_simulation_attempts_tenant_scholar_completed",
            "tenant_id",
            "scholar_id",
            "completed_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scholar_id: Mapped[str] = mapped_column(String(64), nullable=False)
    simulation_type: Mapped[CareerSimulationType] = mapped_column(
        SAEnum(
            CareerSimulationType,
            name="career_simulation_type",
            values_callable=role_values,
            native_enum=True,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    attempt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class ScholarFETCapitalAccount(Base):
    """One tenant-scoped e-currency account for a scholar's FET simulations."""

    __tablename__ = "scholar_fet_capital_accounts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "scholar_id",
            name="uq_scholar_fet_capital_accounts_tenant_scholar",
        ),
        Index(
            "ix_scholar_fet_capital_accounts_tenant_scholar",
            "tenant_id",
            "scholar_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scholar_id: Mapped[str] = mapped_column(String(64), nullable=False)
    balance: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    taxable_profit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    corporate_tax_provision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    vat_output: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vat_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vat_provision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class ScholarFETCapitalTransaction(Base):
    """Append-only, tenant- and scholar-scoped FET capital ledger row."""

    __tablename__ = "scholar_fet_capital_transactions"
    __table_args__ = (
        Index(
            "ix_scholar_fet_capital_transactions_account_created",
            "account_id",
            "created_at",
        ),
        UniqueConstraint(
            "tenant_id",
            "scholar_id",
            "idempotency_key",
            name="uq_scholar_fet_capital_transactions_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("scholar_fet_capital_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scholar_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(240), nullable=False)
    grade_year: Mapped[int] = mapped_column(Integer, nullable=False)
    taxable_profit_delta: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    depreciable_asset_cost_delta: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    transaction_type: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class ScholarPlaygroundTelemetryLog(Base):
    """One completed multi-disciplinary playground game block."""

    __tablename__ = "scholar_playground_telemetry_logs"
    __table_args__ = (
        CheckConstraint(
            "time_spent_seconds >= 0",
            name="ck_playground_telemetry_time_spent",
        ),
        CheckConstraint(
            "successful_moves_count >= 0",
            name="ck_playground_telemetry_successful_moves",
        ),
        CheckConstraint(
            "errors_encountered_count >= 0",
            name="ck_playground_telemetry_errors",
        ),
        Index(
            "ix_playground_telemetry_tenant_scholar_timestamp",
            "tenant_id",
            "scholar_id",
            "timestamp",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scholar_id: Mapped[str] = mapped_column(String(64), nullable=False)
    game_type: Mapped[PlaygroundGameType] = mapped_column(
        SAEnum(
            PlaygroundGameType,
            name="playground_game_type",
            values_callable=role_values,
            native_enum=True,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    level_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    difficulty_tier: Mapped[str] = mapped_column(
        String(16), nullable=False, default="FOUNDATION"
    )
    grade_phase_context: Mapped[str] = mapped_column(
        String(32), nullable=False, default="FOUNDATION_PHASE"
    )
    daily_mastery_quest_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    daily_mastery_quest_play_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    profile_velocity_xp_multiplier: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("1.00")
    )
    active_language: Mapped[SouthAfricanLanguage | None] = mapped_column(
        SAEnum(
            SouthAfricanLanguage,
            name="south_african_language",
            values_callable=role_values,
            native_enum=True,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=True,
    )
    time_spent_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    successful_moves_count: Mapped[int] = mapped_column(Integer, nullable=False)
    errors_encountered_count: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class ScholarWorkItem(Base):
    """Tenant-scoped homework, project, and scholar-created workbook record."""

    __tablename__ = "scholar_work_items"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('HOMEWORK', 'PROJECT', 'PERSONAL_NOTES')",
            name="ck_scholar_work_item_kind",
        ),
        CheckConstraint(
            "origin IN ('TEACHER_ISSUED', 'SCHOLAR_CREATED')",
            name="ck_scholar_work_item_origin",
        ),
        CheckConstraint(
            "status IN ('ASSIGNED', 'IN_PROGRESS', 'SUBMITTED', 'REVIEWED')",
            name="ck_scholar_work_item_status",
        ),
        CheckConstraint(
            "caps_baseline_proficiency IS NULL OR "
            "(caps_baseline_proficiency >= 0 AND caps_baseline_proficiency <= 100)",
            name="ck_scholar_work_item_caps_baseline",
        ),
        CheckConstraint(
            "school_target_proficiency IS NULL OR "
            "(school_target_proficiency >= 0 AND school_target_proficiency <= 100)",
            name="ck_scholar_work_item_school_target",
        ),
        CheckConstraint(
            "reviewed_proficiency IS NULL OR "
            "(reviewed_proficiency >= 0 AND reviewed_proficiency <= 100)",
            name="ck_scholar_work_item_reviewed_proficiency",
        ),
        Index(
            "ix_scholar_work_items_tenant_scholar_created",
            "tenant_id",
            "scholar_user_id",
            "created_at",
        ),
        Index(
            "ix_scholar_work_items_tenant_author_created",
            "tenant_id",
            "created_by_user_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    scholar_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="IN_PROGRESS", server_default="IN_PROGRESS"
    )
    grade_level: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    teacher_instructions: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    scholar_notes: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    deliverables_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    project_plan_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    resource_snapshot_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    support_steps_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    caps_target_snapshot_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    caps_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    caps_baseline_proficiency: Mapped[float | None] = mapped_column(nullable=True)
    school_target_proficiency: Mapped[float | None] = mapped_column(nullable=True)
    reviewed_proficiency: Mapped[float | None] = mapped_column(nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class CapsPracticeTargetLibrary(Base):
    """An educator-reviewed CAPS target reusable within one school tenant."""

    __tablename__ = "caps_practice_target_library"
    __table_args__ = (
        CheckConstraint("grade BETWEEN 0 AND 12", name="ck_caps_target_library_grade"),
        CheckConstraint("term BETWEEN 1 AND 4", name="ck_caps_target_library_term"),
        CheckConstraint(
            "target_mode IN ('CONFIRM', 'OVERRIDE')",
            name="ck_caps_target_library_mode",
        ),
        CheckConstraint(
            "baseline_proficiency BETWEEN 0 AND 100",
            name="ck_caps_target_library_baseline",
        ),
        CheckConstraint(
            "school_target_proficiency BETWEEN 0 AND 100",
            name="ck_caps_target_library_school_target",
        ),
        Index(
            "ix_caps_target_library_tenant_dimensions",
            "tenant_id",
            "grade",
            "subject",
            "term",
            "is_active",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    grade: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(120), nullable=False)
    term: Mapped[int] = mapped_column(Integer, nullable=False)
    catalog_key: Mapped[str] = mapped_column(String(255), nullable=False)
    target_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    target_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    override_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_proficiency: Mapped[float] = mapped_column(Float, nullable=False)
    school_target_proficiency: Mapped[float] = mapped_column(Float, nullable=False)
    target_snapshot_json: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False
    )
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    retired_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


class ScholarWorkReview(Base):
    """Append-only teacher review evidence used to explain practice suggestions."""

    __tablename__ = "scholar_work_reviews"
    __table_args__ = (
        CheckConstraint(
            "reviewed_proficiency >= 0 AND reviewed_proficiency <= 100",
            name="ck_scholar_work_review_proficiency",
        ),
        Index(
            "ix_scholar_work_reviews_tenant_scholar_reviewed",
            "tenant_id",
            "scholar_user_id",
            "reviewed_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("school_tenants.id", ondelete="CASCADE"), nullable=False
    )
    work_item_id: Mapped[int] = mapped_column(
        ForeignKey("scholar_work_items.id", ondelete="CASCADE"), nullable=False
    )
    scholar_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reviewed_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reviewed_proficiency: Mapped[float] = mapped_column(nullable=False)
    caps_baseline_proficiency: Mapped[float | None] = mapped_column(nullable=True)
    school_target_proficiency: Mapped[float | None] = mapped_column(nullable=True)
    caps_target_snapshot_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class GamifiedEmpathyBadge(Base):
    """A localized, tenant-scoped recognition badge for one scholar."""

    __tablename__ = "gamified_empathy_badges"
    __table_args__ = (
        Index(
            "ix_gamified_empathy_badges_tenant_scholar_timestamp",
            "tenant_id",
            "scholar_id",
            "timestamp",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scholar_id: Mapped[str] = mapped_column(String(64), nullable=False)
    badge_type: Mapped[GamifiedEmpathyBadgeType] = mapped_column(
        SAEnum(
            GamifiedEmpathyBadgeType,
            name="gamified_empathy_badge_type",
            values_callable=role_values,
            native_enum=True,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    localized_label_text: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


ROLE_SEEDS: tuple[tuple[UserRole, str, str], ...] = (
    (UserRole.MASTER_ADMIN, "MASTER_ADMIN_PROFILE", "Master Admin"),
    (UserRole.PRINCIPAL, "PRINCIPAL_PROFILE", "Principal"),
    (UserRole.GRADE_HOD, "GRADE_HOD_PROFILE", "Grade HOD"),
    (UserRole.EDUCATOR, "EDUCATOR_PROFILE", "Educator"),
    (UserRole.SCHOLAR, "SCHOLAR_PROFILE", "Scholar"),
    (UserRole.PARENT, "PARENT_PROFILE", "Parent"),
)


class ScholarCohortAssignment(Base):
    """Tenant-owned cohort placement for a provisioned scholar account."""

    __tablename__ = "scholar_cohort_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_account_number",
            "scholar_id",
            name="uq_scholar_cohort_assignment_tenant_scholar",
        ),
        Index(
            "ix_scholar_cohort_assignment_tenant_grade_cohort",
            "tenant_account_number",
            "grade_level",
            "cohort_name_string",
        ),
        CheckConstraint(
            "allocation_status IN "
            "('PENDING_HOD_TEACHER_ALLOCATION', 'ACADEMICALLY_ACTIVE')",
            name="ck_scholar_cohort_assignment_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_account_number: Mapped[str] = mapped_column(
        String(9),
        ForeignKey("school_tenants.account_identifier", ondelete="CASCADE"),
        nullable=False,
    )
    scholar_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.public_id", ondelete="CASCADE"),
        nullable=False,
    )
    grade_level: Mapped[str] = mapped_column(String(32), nullable=False)
    cohort_name_string: Mapped[str] = mapped_column(String(120), nullable=False)
    dynamic_suffix: Mapped[str] = mapped_column(
        String(80), nullable=False, default=""
    )
    allocation_status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="PENDING_HOD_TEACHER_ALLOCATION",
        server_default="PENDING_HOD_TEACHER_ALLOCATION",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )


class CohortEducatorMapping(Base):
    """A tenant-, grade-, cohort-, and subject-scoped educator allocation."""

    __tablename__ = "cohort_educator_mappings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_account_number",
            "grade_level",
            "cohort_name_string",
            "subject_name",
            "educator_user_id",
            name="uq_cohort_educator_mapping_assignment",
        ),
        Index(
            "ix_cohort_educator_mapping_tenant_scope",
            "tenant_account_number",
            "grade_level",
            "cohort_name_string",
        ),
    )

    id: Mapped[int] = mapped_column(
        AUTO_INCREMENT_ID, primary_key=True, autoincrement=True
    )
    tenant_account_number: Mapped[str] = mapped_column(
        String(9),
        ForeignKey("school_tenants.account_identifier", ondelete="CASCADE"),
        nullable=False,
    )
    grade_level: Mapped[str] = mapped_column(String(32), nullable=False)
    cohort_name_string: Mapped[str] = mapped_column(String(120), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(160), nullable=False)
    educator_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_finalized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
