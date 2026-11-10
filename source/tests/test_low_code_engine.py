from __future__ import annotations

import hashlib
import hmac
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth import hash_session_token
from backend.authentication.login_gate import SESSION_COOKIE_NAME
from backend.main import create_app
from backend.models import (
    AcademicYearPlanner,
    AuthSession,
    Base,
    ClassroomHandshake,
    ClassStreamLedger,
    CurriculumPacingLog,
    EducatorProfile,
    GradeHodProfile,
    LessonProgress,
    LowCodeUserProjectSnapshot,
    LowCodeWebsiteSnapshot,
    LowCodeWorkspaceFolder,
    LowCodeWorkspaceFolderItem,
    PublishedWebsiteRevision,
    SchoolTenant,
    SchoolTenantMembership,
    StudentAcademicEnrollment,
    TenantCanvasBlueprint,
    User,
    UserRole,
)
from backend.routes.classroom_periods import local_clock


class RecordingHub:
    def __init__(self) -> None:
        self.broadcasts: list[dict[str, object]] = []

    async def broadcast(
        self,
        tenant_id: int,
        dashboard_viewport: str,
        message: dict[str, object],
        recipient_user_ids: set[int] | None = None,
    ) -> None:
        self.broadcasts.append(
            {
                "tenant_id": tenant_id,
                "dashboard_viewport": dashboard_viewport,
                "message": message,
                "recipient_user_ids": recipient_user_ids,
            }
        )


class LowCodeEngineRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, future=True)
        self.env = patch.dict(os.environ, {"SESSION_SECRET": "low-code-test-secret"})
        self.env.start()
        self.tokens = {
            "principal": "low-code-principal-token",
            "educator": "low-code-educator-token",
            "hod": "low-code-hod-token",
            "scholar": "low-code-scholar-token",
            "other_scholar": "low-code-other-scholar-token",
            "master_admin": "low-code-master-admin-token",
        }

        with Session(self.engine) as session:
            tenant = SchoolTenant(name="Low Code School", slug="low-code-school")
            principal = User(
                profile_code="LOW-CODE-PRINCIPAL",
                display_name="Principal Example",
                role=UserRole.PRINCIPAL,
            )
            educator = User(
                profile_code="LOW-CODE-EDUCATOR",
                display_name="Educator Example",
                role=UserRole.EDUCATOR,
            )
            hod = User(
                profile_code="LOW-CODE-HOD",
                display_name="HOD Example",
                role=UserRole.GRADE_HOD,
            )
            scholar = User(
                profile_code="LOW-CODE-SCHOLAR",
                display_name="Scholar Example",
                role=UserRole.SCHOLAR,
            )
            other_educator = User(
                profile_code="LOW-CODE-OTHER-EDUCATOR",
                display_name="Other Educator",
                role=UserRole.EDUCATOR,
            )
            other_scholar = User(
                profile_code="LOW-CODE-OTHER-SCHOLAR",
                display_name="Other Scholar",
                role=UserRole.SCHOLAR,
            )
            master_admin = User(
                profile_code="LOW-CODE-MASTER-ADMIN",
                display_name="Master Admin Example",
                role=UserRole.MASTER_ADMIN,
            )
            session.add_all(
                [
                    tenant,
                    principal,
                    educator,
                    hod,
                    scholar,
                    other_educator,
                    other_scholar,
                    master_admin,
                ]
            )
            session.flush()

            session.add_all(
                [
                    EducatorProfile(
                        user_id=educator.id,
                        grade_tier="Grade 5",
                        class_identifier="5A",
                    ),
                    EducatorProfile(
                        user_id=other_educator.id,
                        grade_tier="Grade 5",
                        class_identifier="5B",
                    ),
                    GradeHodProfile(
                        user_id=hod.id,
                        grade_tier="Grade 5",
                        department_scope="Mathematics",
                    ),
                ]
            )
            session.flush()

            stream = ClassStreamLedger(
                tenant_id=tenant.id,
                grade_year="Grade 5",
                stream_package="5A",
                display_name="Grade 5A",
                educator_id=educator.id,
            )
            other_stream = ClassStreamLedger(
                tenant_id=tenant.id,
                grade_year="Grade 5",
                stream_package="5B",
                display_name="Grade 5B",
                educator_id=other_educator.id,
            )
            session.add_all([stream, other_stream])
            session.flush()

            session.add_all(
                [
                    StudentAcademicEnrollment(
                        tenant_id=tenant.id,
                        scholar_id=scholar.id,
                        class_stream_id=stream.id,
                        academic_year="2026",
                        enrollment_status="ACTIVE",
                    ),
                    StudentAcademicEnrollment(
                        tenant_id=tenant.id,
                        scholar_id=other_scholar.id,
                        class_stream_id=other_stream.id,
                        academic_year="2026",
                        enrollment_status="ACTIVE",
                    ),
                ]
            )

            for user in [
                principal,
                educator,
                hod,
                scholar,
                other_educator,
                other_scholar,
                master_admin,
            ]:
                session.add(
                    SchoolTenantMembership(
                        school_tenant_id=tenant.id,
                        user_id=user.id,
                    )
                )

            local_today = local_clock().date()
            planner = AcademicYearPlanner(
                tenant_id=tenant.id,
                grade_level="Grade 5",
                curriculum_standard="CAPS",
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                syllabus_map_json={
                    "grade_level": "Grade 5",
                    "chapters": [
                        {
                            "chapter_identifier": "CHAPTER-5-1",
                            "chapter_title": "Fractions",
                            "planned_start_week": 1,
                            "planned_end_week": 1,
                            "target_start_date": (local_today - timedelta(days=2)).isoformat(),
                            "target_end_date": (local_today - timedelta(days=1)).isoformat(),
                            "target_shift_days": 0,
                        }
                    ],
                },
            )
            session.add(planner)
            session.flush()

            user_by_key = {
                "principal": principal,
                "educator": educator,
                "hod": hod,
                "scholar": scholar,
                "other_scholar": other_scholar,
                "master_admin": master_admin,
            }
            for key, user in user_by_key.items():
                session.add(
                    AuthSession(
                        user_id=user.id,
                        token_hash=hash_session_token(self.tokens[key]),
                        expires_at=datetime.utcnow() + timedelta(hours=2),
                        action_context={
                            "classStreamId": stream.id,
                            "plannerId": planner.id,
                        },
                    )
                )
            session.commit()

            self.tenant_id = tenant.id
            self.principal_id = principal.id
            self.educator_id = educator.id
            self.hod_id = hod.id
            self.scholar_id = scholar.id
            self.master_admin_id = master_admin.id
            self.scholar_public_id = scholar.public_id
            self.other_scholar_public_id = other_scholar.public_id
            self.stream_id = stream.id
            self.other_stream_id = other_stream.id
            self.planner_id = planner.id
            self.local_today = local_today

        self.app = create_app(self.session_factory)
        self.hub = RecordingHub()
        self.app.state.canvas_layout_stream_hub = self.hub

    def tearDown(self) -> None:
        self.env.stop()
        self.engine.dispose()

    def client(
        self,
        role_key: str,
        *,
        include_studio_tenant: bool = True,
        studio_tenant_id: int | None = None,
    ) -> TestClient:
        client = TestClient(self.app, raise_server_exceptions=False)
        client.cookies.set(SESSION_COOKIE_NAME, self.tokens[role_key])
        if role_key == "master_admin" and include_studio_tenant:
            selected_tenant_id = (
                self.tenant_id if studio_tenant_id is None else studio_tenant_id
            )
            client.headers.update(
                {"X-Fieldnotes-Studio-Tenant": str(selected_tenant_id)}
            )
        return client

    def test_master_admin_studio_selection_requires_a_school_when_multiple_are_active(
        self,
    ) -> None:
        no_selection_client = self.client(
            "master_admin",
            include_studio_tenant=False,
        )
        single_tenant_context = no_selection_client.get(
            "/api/studio/projects/tenant-context"
        )
        self.assertEqual(single_tenant_context.status_code, 200)
        self.assertEqual(
            single_tenant_context.json()["selectedTenantId"],
            self.tenant_id,
        )
        self.assertEqual(
            no_selection_client.get("/api/studio/projects/folders").status_code,
            200,
        )

        with Session(self.engine) as session:
            second_tenant = SchoolTenant(
                name="Second Studio School",
                slug="second-studio-school",
            )
            session.add(second_tenant)
            session.commit()
            second_tenant_id = second_tenant.id

        context_response = no_selection_client.get(
            "/api/studio/projects/tenant-context"
        )
        self.assertEqual(context_response.status_code, 200)
        self.assertIsNone(context_response.json()["selectedTenantId"])
        self.assertTrue(context_response.json()["selectionRequired"])
        self.assertEqual(
            len(context_response.json()["tenants"]),
            2,
        )

        missing_selection = no_selection_client.get(
            "/api/studio/projects/folders"
        )
        self.assertEqual(missing_selection.status_code, 409)
        self.assertEqual(
            missing_selection.json()["detail"]["code"],
            "studio_tenant_selection_required",
        )

        invalid_selection = self.client(
            "master_admin",
            studio_tenant_id=999_999,
        ).get("/api/studio/websites/list")
        self.assertEqual(invalid_selection.status_code, 409)

        selected_school_client = self.client(
            "master_admin",
            studio_tenant_id=second_tenant_id,
        )
        self.assertEqual(
            selected_school_client.get("/api/studio/projects/folders").status_code,
            200,
        )
        self.assertEqual(
            selected_school_client.get("/api/studio/websites/list").status_code,
            200,
        )

    def test_semantic_website_snapshots_are_validated_tenant_scoped_and_deletable(self) -> None:
        semantic_blueprint = {
            "schemaVersion": 1,
            "templateType": "landing-saas",
            "sections": [
                {
                    "id": "hero",
                    "semanticTag": "section",
                    "label": "Landing hero",
                    "columns": 1,
                    "styles": {
                        "color": "#302B27",
                        "backgroundColor": "#FFFFFF",
                    },
                    "children": [
                        {
                            "id": "hero-heading",
                            "type": "heading2",
                            "x": 48,
                            "y": 72,
                            "width": 320,
                            "height": 180,
                            "data": {
                                "content": "Build a clearer web presence",
                                "source": "website-editor",
                            },
                            "componentType": "heading2",
                            "semanticTag": "h2",
                            "label": "Section Heading <h2>",
                            "content": "Build a clearer web presence",
                            "styles": {
                                "color": "#302B27",
                                "backgroundColor": "#FFFFFF",
                            },
                        }
                    ],
                }
            ],
        }
        request_body = {
            "siteTitle": "Studio website",
            "selectedWebTemplateType": "landing-saas",
            "semanticBlueprintData": semantic_blueprint,
        }
        self.assertEqual(
            self.client("principal").post(
                "/api/studio/websites/save",
                json=request_body,
            ).status_code,
            403,
        )

        with Session(self.engine) as session:
            foreign_tenant = SchoolTenant(
                name="Other Website School",
                slug="other-website-school",
            )
            session.add(foreign_tenant)
            session.flush()
            foreign_site = LowCodeWebsiteSnapshot(
                site_id="foreign-website-snapshot",
                site_title="Foreign website",
                selected_web_template_type="landing-saas",
                semantic_blueprint_data=semantic_blueprint,
                tenant_id=foreign_tenant.id,
            )
            session.add(foreign_site)
            session.commit()

        created = self.client("master_admin").post(
            "/api/studio/websites/save",
            json=request_body,
        )
        self.assertEqual(created.status_code, 201)
        site_id = created.json()["siteId"]
        saved_blueprint = created.json()["semanticBlueprintData"]
        self.assertEqual(saved_blueprint["templateType"], "landing-saas")
        self.assertEqual(
            saved_blueprint["sections"][0]["children"][0]["content"],
            "Build a clearer web presence",
        )
        saved_component = saved_blueprint["sections"][0]["children"][0]
        self.assertEqual(
            {
                key: saved_component[key]
                for key in ("type", "x", "y", "width", "height")
            },
            {
                "type": "heading2",
                "x": 48,
                "y": 72,
                "width": 320,
                "height": 180,
            },
        )
        self.assertEqual(saved_component["data"]["source"], "website-editor")

        website_list = self.client("master_admin").get(
            "/api/studio/websites/list"
        )
        self.assertEqual(website_list.status_code, 200)
        self.assertEqual([site["siteId"] for site in website_list.json()], [site_id])
        website_folder_response = self.client("master_admin").post(
            "/api/studio/websites/folders",
            json={"folderName": "  Campaign sites  "},
        )
        self.assertEqual(website_folder_response.status_code, 201)
        website_folder = website_folder_response.json()
        self.assertEqual(website_folder["folderName"], "Campaign sites")
        duplicate_folder = self.client("master_admin").post(
            "/api/studio/websites/folders",
            json={"folderName": "campaign SITES"},
        )
        self.assertEqual(duplicate_folder.status_code, 409)

        canvas_folder_response = self.client("master_admin").post(
            "/api/studio/projects/folders",
            json={"folderName": "Canvas-only folder"},
        )
        self.assertEqual(canvas_folder_response.status_code, 201)
        wrong_collection_move = self.client("master_admin").put(
            f"/api/studio/websites/folder/{site_id}",
            json={"folderId": canvas_folder_response.json()["folderId"]},
        )
        self.assertEqual(wrong_collection_move.status_code, 404)
        website_move = self.client("master_admin").put(
            f"/api/studio/websites/folder/{site_id}",
            json={"folderId": website_folder["folderId"]},
        )
        self.assertEqual(website_move.status_code, 200)
        self.assertEqual(website_move.json()["folderId"], website_folder["folderId"])
        website_list = self.client("master_admin").get("/api/studio/websites/list")
        self.assertEqual(website_list.json()[0]["folderId"], website_folder["folderId"])
        self.assertEqual(
            self.client("master_admin").get("/api/studio/websites/folders").json(),
            [website_folder],
        )
        self.assertEqual(
            self.client("master_admin").get(
                "/api/studio/websites/read/foreign-website-snapshot"
            ).status_code,
            404,
        )

        unsafe_blueprint = json.loads(json.dumps(semantic_blueprint))
        unsafe_blueprint["sections"][0]["children"][0]["href"] = "javascript:alert(1)"
        unsafe_response = self.client("master_admin").post(
            "/api/studio/websites/save",
            json={
                **request_body,
                "semanticBlueprintData": unsafe_blueprint,
            },
        )
        self.assertEqual(unsafe_response.status_code, 422)

        out_of_bounds_blueprint = json.loads(json.dumps(semantic_blueprint))
        out_of_bounds_blueprint["sections"][0]["children"][0]["x"] = 9_900
        out_of_bounds_response = self.client("master_admin").post(
            "/api/studio/websites/save",
            json={
                **request_body,
                "semanticBlueprintData": out_of_bounds_blueprint,
            },
        )
        self.assertEqual(out_of_bounds_response.status_code, 422)

        updated = self.client("master_admin").post(
            "/api/studio/websites/save",
            json={
                **request_body,
                "siteId": site_id,
                "siteTitle": "Updated studio website",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["siteTitle"], "Updated studio website")
        self.assertEqual(updated.json()["folderId"], website_folder["folderId"])
        self.assertEqual(
            updated.json()["semanticBlueprintData"]["sections"][0]["children"][0]["x"],
            48,
        )

        foreign_delete = self.client("master_admin").delete(
            "/api/studio/websites/delete/foreign-website-snapshot"
        )
        self.assertEqual(foreign_delete.status_code, 404)
        with Session(self.engine) as session:
            target_site = session.get(LowCodeWebsiteSnapshot, site_id)
            foreign_site = session.get(
                LowCodeWebsiteSnapshot,
                "foreign-website-snapshot",
            )
            session.add_all(
                [
                    PublishedWebsiteRevision(
                        tenant_id=target_site.tenant_id,
                        site_id=site_id,
                        revision_number=1,
                        hostname="deleted-low-code-school.fieldnotes.co.za",
                        site_title=target_site.site_title,
                        selected_web_template_type=target_site.selected_web_template_type,
                        semantic_blueprint_data=saved_blueprint,
                        content_sha256="a" * 64,
                    ),
                    PublishedWebsiteRevision(
                        tenant_id=foreign_site.tenant_id,
                        site_id=foreign_site.site_id,
                        revision_number=1,
                        hostname="retained-other-school.fieldnotes.co.za",
                        site_title=foreign_site.site_title,
                        selected_web_template_type=foreign_site.selected_web_template_type,
                        semantic_blueprint_data=foreign_site.semantic_blueprint_data,
                        content_sha256="b" * 64,
                    ),
                ]
            )
            session.commit()

        delete_response = self.client("master_admin").delete(
            f"/api/studio/websites/delete/{site_id}"
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["deleted"])
        self.assertEqual(
            delete_response.json()["publishedRevisionsDeleted"],
            1,
        )
        self.assertEqual(
            self.client("master_admin").get(
                "/api/studio/websites/list"
            ).json(),
            [],
        )
        with Session(self.engine) as session:
            self.assertIsNone(session.get(LowCodeWebsiteSnapshot, site_id))
            self.assertIsNone(
                session.scalar(
                    select(LowCodeWorkspaceFolderItem).where(
                        LowCodeWorkspaceFolderItem.item_id == site_id,
                        LowCodeWorkspaceFolderItem.collection == "semantic-websites",
                    )
                )
            )
            self.assertIsNotNone(
                session.get(
                    LowCodeWebsiteSnapshot,
                    "foreign-website-snapshot",
                )
            )
            self.assertIsNone(
                session.scalar(
                    select(PublishedWebsiteRevision).where(
                        PublishedWebsiteRevision.site_id == site_id,
                    )
                )
            )
            self.assertIsNotNone(
                session.scalar(
                    select(PublishedWebsiteRevision).where(
                        PublishedWebsiteRevision.site_id
                        == "foreign-website-snapshot",
                    )
                )
            )
        deleted_public_site = self.client("master_admin").get(
            "/api/public/websites/resolve",
            params={"hostname": "deleted-low-code-school.fieldnotes.co.za"},
        )
        self.assertEqual(deleted_public_site.status_code, 404)
        self.assertEqual(
            deleted_public_site.headers["cache-control"],
            "no-store",
        )
        self.assertEqual(
            self.hub.broadcasts[-1]["message"]["type"],
            "website.snapshots.updated",
        )
        self.assertEqual(
            self.hub.broadcasts[-1]["message"]["action"],
            "deleted",
        )

    def add_previous_day_progress(self, scholar_id: int | None = None) -> None:
        with Session(self.engine) as session:
            session.add(
                LessonProgress(
                    tenant_id=self.tenant_id,
                    scholar_id=scholar_id or self.scholar_id,
                    class_stream_id=self.stream_id,
                    lesson_key="fractions-lesson-1",
                    active_topic_key="equivalent-fractions",
                    subject="Mathematics",
                    subject_label="Mathematics",
                    title="Equivalent fractions",
                    content_json={"summaryPoints": ["Use equivalent values."]},
                    status="PAUSED",
                    period_date=self.local_today - timedelta(days=1),
                    initiated_at=datetime.utcnow() - timedelta(days=1),
                    updated_at=datetime.utcnow() - timedelta(hours=1),
                    completed_at=None,
                )
            )
            session.commit()

    def test_page_search_metadata_persists_and_rejects_unsafe_social_images(self) -> None:
        home_section = {
            "id": "home-section",
            "semanticTag": "main",
            "label": "Home content",
            "children": [],
        }
        about_section = {
            "id": "about-section",
            "semanticTag": "main",
            "label": "About content",
            "children": [],
        }
        blueprint = {
            "schemaVersion": 1,
            "templateType": "landing-saas",
            "activePageId": "page-home",
            "sections": [home_section],
            "pages": [
                {
                    "id": "page-home",
                    "title": "Home",
                    "slug": "home",
                    "seoTitle": "Home search title",
                    "seoDescription": "A helpful home page summary.",
                    "socialImageUrl": "https://cdn.example.com/home-share.png",
                    "sections": [home_section],
                },
                {
                    "id": "page-about",
                    "title": "About",
                    "slug": "about",
                    "seoTitle": "About search title",
                    "seoDescription": "A different page summary.",
                    "socialImageUrl": "/assets/about-share.webp",
                    "sections": [about_section],
                },
            ],
        }
        payload = {
            "siteTitle": "Metadata website",
            "selectedWebTemplateType": "landing-saas",
            "semanticBlueprintData": blueprint,
        }

        created = self.client("master_admin").post(
            "/api/studio/websites/save",
            json=payload,
        )

        self.assertEqual(created.status_code, 201)
        saved_pages = created.json()["semanticBlueprintData"]["pages"]
        self.assertEqual(saved_pages[0]["seoTitle"], "Home search title")
        self.assertEqual(
            saved_pages[0]["seoDescription"],
            "A helpful home page summary.",
        )
        self.assertEqual(
            saved_pages[0]["socialImageUrl"],
            "https://cdn.example.com/home-share.png",
        )
        self.assertEqual(saved_pages[1]["socialImageUrl"], "/assets/about-share.webp")
        loaded = self.client("master_admin").get(
            f"/api/studio/websites/read/{created.json()['siteId']}"
        )
        self.assertEqual(
            loaded.json()["semanticBlueprintData"]["pages"][1]["seoTitle"],
            "About search title",
        )

        unsafe_blueprint = json.loads(json.dumps(blueprint))
        unsafe_blueprint["pages"][0]["socialImageUrl"] = "javascript:alert(1)"
        rejected = self.client("master_admin").post(
            "/api/studio/websites/save",
            json={**payload, "semanticBlueprintData": unsafe_blueprint},
        )
        self.assertEqual(rejected.status_code, 422)

    def test_core_workspaces_can_be_foldered_without_becoming_deletable(self) -> None:
        client = self.client("master_admin")
        folder_response = client.post(
            "/api/studio/projects/folders",
            json={"folderName": "Protected workspaces"},
        )
        self.assertEqual(folder_response.status_code, 201)
        folder_id = folder_response.json()["folderId"]

        move_response = client.put(
            "/api/studio/projects/core-workspaces/scholar/folder",
            json={"folderId": folder_id},
        )
        self.assertEqual(move_response.status_code, 200, move_response.text)
        self.assertEqual(
            move_response.json(),
            {
                "workspaceId": "scholar",
                "folderId": folder_id,
                "folderName": "Protected workspaces",
            },
        )
        folders = client.get("/api/studio/projects/folders").json()
        self.assertEqual(folders[0]["coreWorkspaceIds"], ["scholar"])

        with Session(self.engine) as session:
            other_tenant = SchoolTenant(
                name="Other Core Workspace School",
                slug="other-core-workspace-school",
            )
            session.add(other_tenant)
            session.flush()
            foreign_folder = LowCodeWorkspaceFolder(
                tenant_id=other_tenant.id,
                collection="canvas-projects",
                folder_name="Foreign folder",
                folder_key="foreign folder",
            )
            session.add(foreign_folder)
            session.flush()
            foreign_folder_id = foreign_folder.folder_id
            session.commit()

        self.assertEqual(
            client.put(
                "/api/studio/projects/core-workspaces/healthcare/folder",
                json={"folderId": foreign_folder_id},
            ).status_code,
            404,
        )
        self.assertEqual(
            client.put(
                "/api/studio/projects/core-workspaces/not-a-core-workspace/folder",
                json={"folderId": folder_id},
            ).status_code,
            404,
        )

        unfile_response = client.put(
            "/api/studio/projects/core-workspaces/scholar/folder",
            json={"folderId": None},
        )
        self.assertEqual(unfile_response.status_code, 200)
        self.assertIsNone(unfile_response.json()["folderId"])
        self.assertEqual(
            client.get("/api/studio/projects/folders").json()[0][
                "coreWorkspaceIds"
            ],
            [],
        )
        for workspace_id in ("scholar", "healthcare"):
            self.assertEqual(
                client.delete(
                    f"/api/studio/projects/delete/{workspace_id}"
                ).status_code,
                404,
            )

    def test_project_snapshots_are_validated_tenant_scoped_and_deletable(self) -> None:
        layout = {
            "schemaVersion": 1,
            "layoutMode": "grid",
            "gridColumns": 2,
            "gridRows": 1,
            "gap": 20,
            "canvasHeight": 480,
            "canvasLayout": [],
            "subTabs": [
                {
                    "tabId": "studio-snapshot-tab",
                    "tabLabelDisplayName": "Snapshot tab",
                    "displayOrderWeight": 0,
                    "status": "active",
                    "layoutMode": "grid",
                    "gridColumns": 2,
                    "gridRows": 1,
                    "gap": 20,
                    "canvasHeight": 480,
                    "canvasLayout": [
                        {
                            "id": "snapshot-block-1",
                            "type": "text_observation_block",
                            "label": "Safe text block",
                            "variableBinding": "CURRENT_PROFILE_NAME",
                            "functionBinding": "TRIGGER_OTP_GATE",
                            "visibility": {"master_admin": True},
                            "gridColumn": 1,
                            "gridRow": 1,
                            "gridColumnSpan": 1,
                            "gridRowSpan": 1,
                            "x": 5,
                            "y": 8,
                            "width": 40,
                            "height": 24,
                            "styles": {
                                "color": "#123456",
                                "backgroundColor": "#ABCDEF",
                            },
                        }
                    ],
                },
                {
                    "tabId": "unrelated-studio-tab",
                    "tabLabelDisplayName": "Keep this tab",
                    "displayOrderWeight": 1,
                    "status": "active",
                    "canvasLayout": [],
                },
            ],
            "activeSubTabId": "studio-snapshot-tab",
        }
        request_body = {
            "projectTitle": "  Grade 5 Dashboard  ",
            "selectedBaseLayoutType": "balanced-dual-column",
            "canvasBlueprintData": {
                "layout": layout,
                "designVariables": [],
                "coordinateMatrix": [],
                "colorWheelValues": [],
                "allowlistTokens": {},
            },
        }
        with Session(self.engine) as session:
            session.add(
                TenantCanvasBlueprint(
                    tenant_id=self.tenant_id,
                    dashboard_viewport="Canvas Project Studio",
                    serialized_json_layout=json.dumps(layout),
                )
            )
            session.commit()

        self.assertEqual(
            self.client("principal").get("/api/studio/projects/list").status_code,
            403,
        )
        self.assertEqual(
            self.client("scholar").post(
                "/api/studio/projects/create", json=request_body
            ).status_code,
            403,
        )

        invalid_body = {
            **request_body,
            "canvasBlueprintData": {
                "layout": {
                    **layout,
                    "subTabs": [
                        {
                            **layout["subTabs"][0],
                            "canvasLayout": [
                                {
                                    **layout["subTabs"][0]["canvasLayout"][0],
                                    "functionBinding": "window.alert(1)",
                                }
                            ],
                        }
                    ],
                }
            },
        }
        self.assertEqual(
            self.client("master_admin").post(
                "/api/studio/projects/create", json=invalid_body
            ).status_code,
            422,
        )

        principal_only_snapshot = {
            **request_body,
            "canvasBlueprintData": {
                **request_body["canvasBlueprintData"],
                "layout": {
                    **layout,
                    "subTabs": [
                        {
                            **layout["subTabs"][0],
                            "canvasLayout": [
                                {
                                    "id": "principal-only-snapshot",
                                    "type": "master_overview_container",
                                    "label": "Master Overview",
                                    "children": [],
                                }
                            ],
                        }
                    ],
                },
            },
        }
        self.assertEqual(
            self.client("master_admin").post(
                "/api/studio/projects/create",
                json=principal_only_snapshot,
            ).status_code,
            422,
        )

        create_response = self.client("master_admin").post(
            "/api/studio/projects/create",
            json=request_body,
        )
        self.assertEqual(create_response.status_code, 201, create_response.text)
        created = create_response.json()
        self.assertEqual(created["projectTitle"], "Grade 5 Dashboard")
        self.assertEqual(created["selectedBaseLayoutType"], "balanced-dual-column")
        canvas_folder_response = self.client("master_admin").post(
            "/api/studio/projects/folders",
            json={"folderName": "School dashboards"},
        )
        self.assertEqual(canvas_folder_response.status_code, 201)
        canvas_folder = canvas_folder_response.json()
        project_move = self.client("master_admin").put(
            f"/api/studio/projects/folder/{created['projectId']}",
            json={"folderId": canvas_folder["folderId"]},
        )
        self.assertEqual(project_move.status_code, 200)
        self.assertEqual(project_move.json()["folderName"], "School dashboards")
        project_update = self.client("master_admin").put(
            f"/api/studio/projects/update/{created['projectId']}",
            json=request_body,
        )
        self.assertEqual(project_update.status_code, 200)
        self.assertEqual(project_update.json()["folderId"], canvas_folder["folderId"])

        with Session(self.engine) as session:
            snapshot = session.get(
                LowCodeUserProjectSnapshot,
                created["projectId"],
            )
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.tenant_id, self.tenant_id)
            self.assertEqual(
                snapshot.projectTitle,
                "Grade 5 Dashboard",
            )
            self.assertEqual(
                snapshot.canvasBlueprintData["coordinateMatrix"][0]["x"],
                5,
            )
            self.assertEqual(
                snapshot.canvasBlueprintData["colorWheelValues"][0]["foreground"],
                "#123456",
            )
            self.assertEqual(
                snapshot.canvasBlueprintData["allowlistTokens"]["actionBindings"],
                ["TRIGGER_OTP_GATE"],
            )
            self.assertEqual(
                snapshot.canvasBlueprintData["studioTabId"],
                "studio-snapshot-tab",
            )
            session.add(
                LowCodeUserProjectSnapshot(
                    project_id="same-tab-secondary-snapshot",
                    project_title="Second saved copy",
                    selected_base_layout_type="balanced-dual-column",
                    canvas_blueprint_data=snapshot.canvas_blueprint_data,
                    created_timestamp=snapshot.created_timestamp
                    + timedelta(seconds=1),
                    tenant_id=self.tenant_id,
                )
            )

            other_tenant = SchoolTenant(
                name="Other Snapshot School",
                slug="other-snapshot-school",
            )
            session.add(other_tenant)
            session.flush()
            foreign_folder = LowCodeWorkspaceFolder(
                tenant_id=other_tenant.id,
                collection="canvas-projects",
                folder_name="Foreign folder",
                folder_key="foreign folder",
            )
            session.add(foreign_folder)
            session.flush()
            foreign_folder_id = foreign_folder.folder_id
            foreign_snapshot = LowCodeUserProjectSnapshot(
                project_id="foreign-snapshot-layout",
                project_title="Foreign layout",
                selected_base_layout_type="custom",
                canvas_blueprint_data={"schemaVersion": 1},
                tenant_id=other_tenant.id,
            )
            session.add(foreign_snapshot)
            session.commit()
            foreign_tenant_id = other_tenant.id

        list_response = self.client("master_admin").get(
            "/api/studio/projects/list"
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(
            {item["projectId"] for item in list_response.json()},
            {created["projectId"], "same-tab-secondary-snapshot"},
        )
        self.assertEqual(
            next(
                item
                for item in list_response.json()
                if item["projectId"] == created["projectId"]
            )["folderId"],
            canvas_folder["folderId"],
        )
        self.assertEqual(
            self.client("master_admin").put(
                f"/api/studio/projects/folder/{created['projectId']}",
                json={"folderId": foreign_folder_id},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client("master_admin").delete(
                "/api/studio/projects/delete/foreign-snapshot-layout"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client("master_admin").delete(
                "/api/studio/projects/delete/scholar"
            ).status_code,
            404,
        )

        delete_response = self.client("master_admin").delete(
            f"/api/studio/projects/delete/{created['projectId']}"
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["deleted"])
        with Session(self.engine) as session:
            self.assertIsNone(
                session.get(LowCodeUserProjectSnapshot, created["projectId"])
            )
            self.assertIsNone(
                session.scalar(
                    select(LowCodeWorkspaceFolderItem).where(
                        LowCodeWorkspaceFolderItem.item_id == created["projectId"],
                        LowCodeWorkspaceFolderItem.collection == "canvas-projects",
                    )
                )
            )
            self.assertIsNotNone(
                session.get(
                    LowCodeUserProjectSnapshot,
                    "same-tab-secondary-snapshot",
                )
            )
            self.assertIsNotNone(
                session.get(LowCodeUserProjectSnapshot, "foreign-snapshot-layout")
            )
            self.assertEqual(foreign_tenant_id != self.tenant_id, True)
            source_blueprint = session.scalar(
                select(TenantCanvasBlueprint).where(
                    TenantCanvasBlueprint.tenant_id == self.tenant_id,
                    TenantCanvasBlueprint.dashboard_viewport
                    == "Canvas Project Studio",
                )
            )
            self.assertIsNotNone(source_blueprint)
            still_referenced_layout = json.loads(
                source_blueprint.serialized_json_layout
            )
            self.assertEqual(
                still_referenced_layout["activeSubTabId"],
                "studio-snapshot-tab",
            )
            self.assertEqual(
                {tab["tabId"] for tab in still_referenced_layout["subTabs"]},
                {"studio-snapshot-tab", "unrelated-studio-tab"},
            )

        second_delete_response = self.client("master_admin").delete(
            "/api/studio/projects/delete/same-tab-secondary-snapshot"
        )
        self.assertEqual(second_delete_response.status_code, 200)
        with Session(self.engine) as session:
            source_blueprint = session.scalar(
                select(TenantCanvasBlueprint).where(
                    TenantCanvasBlueprint.tenant_id == self.tenant_id,
                    TenantCanvasBlueprint.dashboard_viewport
                    == "Canvas Project Studio",
                )
            )
            self.assertIsNotNone(source_blueprint)
            updated_layout = json.loads(source_blueprint.serialized_json_layout)
            self.assertEqual(
                [tab["tabId"] for tab in updated_layout["subTabs"]],
                ["unrelated-studio-tab"],
            )
            self.assertEqual(
                updated_layout["activeSubTabId"],
                "unrelated-studio-tab",
            )
        self.assertEqual(self.hub.broadcasts[-1]["tenant_id"], self.tenant_id)
        self.assertEqual(
            self.hub.broadcasts[-1]["dashboard_viewport"],
            "Global Project Hub",
        )
        self.assertEqual(
            self.hub.broadcasts[-1]["message"]["type"],
            "project.snapshots.updated",
        )
        self.assertEqual(
            self.hub.broadcasts[-2]["dashboard_viewport"],
            "Canvas Project Studio",
        )
        self.assertEqual(
            self.hub.broadcasts[-2]["message"]["type"],
            "layout.updated",
        )

    def test_otp_gate_role_matrix_digest_and_secretless_refresh(self) -> None:
        body = {"actionBinding": "TRIGGER_OTP_GATE"}
        self.assertEqual(self.client("hod").post("/api/low-code/actions/execute", json=body).status_code, 403)
        self.assertEqual(self.client("scholar").post("/api/low-code/actions/execute", json=body).status_code, 403)
        client_target = self.client("educator").post(
            "/api/low-code/actions/execute",
            json={**body, "classStreamId": self.other_stream_id},
        )
        self.assertEqual(client_target.status_code, 422)

        principal_response = self.client("principal").post(
            "/api/low-code/actions/execute", json=body
        )
        self.assertEqual(principal_response.status_code, 200, principal_response.text)
        principal_result = principal_response.json()
        self.assertRegex(principal_result["code"], r"^\d{6}$")
        self.assertEqual(principal_result["targetLabel"], "Grade 5A")
        self.assertEqual(principal_response.headers["cache-control"], "no-store")
        expires_at = datetime.fromisoformat(
            principal_result["expiresAt"].replace("Z", "+00:00")
        )
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        remaining_seconds = (expires_at - datetime.now(timezone.utc)).total_seconds()
        self.assertGreater(remaining_seconds, 280)
        self.assertLessEqual(remaining_seconds, 300)

        expected_digest = hmac.new(
            b"low-code-test-secret",
            principal_result["code"].encode(),
            hashlib.sha256,
        ).hexdigest()
        with Session(self.engine) as session:
            handshake = session.scalar(
                select(ClassroomHandshake).where(
                    ClassroomHandshake.scholar_id == self.scholar_id
                )
            )
            self.assertEqual(handshake.phase, "OTP_GATE")
            self.assertEqual(handshake.response_token_digest, expected_digest)
            self.assertNotEqual(handshake.response_token_digest, principal_result["code"])

        event = self.hub.broadcasts[-1]
        self.assertEqual(event["dashboard_viewport"], "Scholar Simulation Portal")
        self.assertEqual(event["recipient_user_ids"], {self.scholar_id})
        self.assertEqual(event["message"]["type"], "classroom.otp_gate.updated")
        self.assertNotIn("code", event["message"])
        self.assertNotIn(principal_result["code"], str(event["message"]))

        scholar_client = self.client("scholar")
        active = scholar_client.get("/api/scholar/classroom-handshake/active")
        self.assertEqual(active.status_code, 200)
        self.assertEqual(active.json()["phase"], "OTP_GATE")
        self.assertNotIn("code", active.json())
        self.assertNotIn("response_token", active.json())
        self.assertEqual(active.headers["cache-control"], "no-store")

        verified = scholar_client.post(
            "/api/scholar/classroom-handshake/verify",
            json={"response_token": principal_result["code"]},
        )
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertTrue(verified.json()["verified"])
        replay = scholar_client.post(
            "/api/scholar/classroom-handshake/verify",
            json={"response_token": principal_result["code"]},
        )
        self.assertEqual(replay.status_code, 410)

        educator_response = self.client("educator").post(
            "/api/low-code/actions/execute", json=body
        )
        self.assertEqual(educator_response.status_code, 200, educator_response.text)
        self.assertRegex(educator_response.json()["code"], r"^\d{6}$")

    def test_educator_cannot_trigger_gate_for_another_educators_class(self) -> None:
        context_response = self.client("educator").put(
            "/api/low-code/action-context",
            json={
                "classStreamId": self.other_stream_id,
                "plannerId": self.planner_id,
            },
        )
        self.assertEqual(context_response.status_code, 403)

        with Session(self.engine) as session:
            auth_session = session.scalar(
                select(AuthSession).where(AuthSession.user_id == self.educator_id)
            )
            auth_session.action_context = {
                "classStreamId": self.other_stream_id,
                "plannerId": self.planner_id,
            }
            session.commit()

        response = self.client("educator").post(
            "/api/low-code/actions/execute",
            json={"actionBinding": "TRIGGER_OTP_GATE"},
        )
        self.assertEqual(response.status_code, 403)

    def test_rollover_role_matrix_audit_shift_and_targeted_scholar_refresh(self) -> None:
        self.add_previous_day_progress()
        body = {"actionBinding": "ROLLOVER_PAST_PACING"}
        self.assertEqual(self.client("educator").post("/api/low-code/actions/execute", json=body).status_code, 403)
        self.assertEqual(self.client("scholar").post("/api/low-code/actions/execute", json=body).status_code, 403)

        response = self.client("principal").post(
            "/api/low-code/actions/execute", json=body
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["targetStartDate"], (self.local_today - timedelta(days=1)).isoformat())
        self.assertEqual(result["targetEndDate"], self.local_today.isoformat())
        self.assertEqual(result["targetShiftDays"], 1)
        self.assertEqual(result["actorName"], "Principal Example")

        with Session(self.engine) as session:
            planner = session.get(AcademicYearPlanner, self.planner_id)
            audit = planner.syllabus_map_json["pacing_variance_log"]
            self.assertEqual(len(audit), 1)
            self.assertEqual(audit[0]["action"], "ROLLOVER_PAST_PACING")
            self.assertEqual(audit[0]["actor_display_name"], "Principal Example")
            self.assertIn("Principal Example", audit[0]["note"])
            self.assertEqual(
                session.scalars(select(CurriculumPacingLog)).all(),
                [],
                "Rollover must not be misrecorded as a pause event.",
            )

        event = self.hub.broadcasts[-1]
        self.assertEqual(event["message"]["type"], "lesson.progress.refresh")
        self.assertEqual(event["recipient_user_ids"], {self.scholar_id})
        self.assertNotIn("code", event["message"])

    def test_hod_can_roll_over_matching_grade_but_not_another_grade(self) -> None:
        body = {"actionBinding": "ROLLOVER_PAST_PACING"}
        allowed = self.client("hod").post("/api/low-code/actions/execute", json=body)
        self.assertEqual(allowed.status_code, 200, allowed.text)

        with Session(self.engine) as session:
            profile = session.get(GradeHodProfile, self.hod_id)
            profile.grade_tier = "Grade 4"
            session.commit()

        denied = self.client("hod").post("/api/low-code/actions/execute", json=body)
        self.assertEqual(denied.status_code, 403)

    def test_lesson_progress_sync_and_previous_day_lookup_are_roster_scoped(self) -> None:
        now = datetime.now(timezone.utc)
        lesson = {
            "lesson_key": "fractions-lesson-1",
            "class_identifier": "5A",
            "scholar_public_ids": [self.scholar_public_id],
            "active_topic_key": "equivalent-fractions",
            "subject": "Mathematics",
            "subject_label": "Mathematics",
            "title": "Equivalent fractions",
            "content_json": {"summaryPoints": ["Use equivalent values."]},
            "status": "PAUSED",
            "period_date": (self.local_today - timedelta(days=1)).isoformat(),
            "initiated_at": (now - timedelta(hours=2)).isoformat(),
            "updated_at": now.isoformat(),
            "completed_at": None,
        }
        response = self.client("educator").post(
            "/api/low-code/lesson-progress/sync",
            json={"lessons": [lesson]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"synced": 1})

        retrieved = self.client("scholar").get(
            "/api/low-code/scholar/lesson-progress/previous-day"
        )
        self.assertEqual(retrieved.status_code, 200, retrieved.text)
        self.assertEqual(retrieved.headers["cache-control"], "no-store")
        payload = retrieved.json()
        self.assertEqual(payload["previous_day"], lesson["period_date"])
        self.assertEqual(len(payload["lessons"]), 1)
        self.assertEqual(payload["lessons"][0]["lesson_key"], lesson["lesson_key"])
        self.assertEqual(payload["lessons"][0]["content_json"], lesson["content_json"])

        current_lesson = {
            **lesson,
            "lesson_key": "fractions-lesson-current",
            "status": "INITIATED",
            "period_date": self.local_today.isoformat(),
            "initiated_at": now.isoformat(),
        }
        current_sync = self.client("educator").post(
            "/api/low-code/lesson-progress/sync",
            json={"lessons": [current_lesson]},
        )
        self.assertEqual(current_sync.status_code, 200, current_sync.text)

        active = self.client("scholar").get(
            "/api/low-code/scholar/lesson-progress/active"
        )
        self.assertEqual(active.status_code, 200, active.text)
        self.assertEqual(active.headers["cache-control"], "no-store")
        self.assertEqual(active.json()["current_day"], self.local_today.isoformat())
        self.assertEqual(
            [entry["lesson_key"] for entry in active.json()["lessons"]],
            ["fractions-lesson-current"],
        )
        self.assertEqual(active.json()["lessons"][0]["class_identifier"], "5A")
        self.assertEqual(
            active.json()["lessons"][0]["content_json"],
            current_lesson["content_json"],
        )
        other_scholar_active = self.client("other_scholar").get(
            "/api/low-code/scholar/lesson-progress/active"
        )
        self.assertEqual(other_scholar_active.status_code, 200)
        self.assertEqual(other_scholar_active.json()["lessons"], [])

        wrong_roster = {**lesson, "scholar_public_ids": [self.other_scholar_public_id]}
        denied = self.client("educator").post(
            "/api/low-code/lesson-progress/sync",
            json={"lessons": [wrong_roster]},
        )
        self.assertEqual(denied.status_code, 403)

        denied_role = self.client("principal").post(
            "/api/low-code/lesson-progress/sync",
            json={"lessons": [lesson]},
        )
        self.assertEqual(denied_role.status_code, 403)

    def test_progress_sync_rejects_inactive_tenant_membership(self) -> None:
        with Session(self.engine) as session:
            membership = session.scalar(
                select(SchoolTenantMembership).where(
                    SchoolTenantMembership.school_tenant_id == self.tenant_id,
                    SchoolTenantMembership.user_id == self.scholar_id,
                )
            )
            membership.is_active = False
            session.commit()

        now = datetime.now(timezone.utc)
        response = self.client("educator").post(
            "/api/low-code/lesson-progress/sync",
            json={
                "lessons": [
                    {
                        "lesson_key": "inactive-member-lesson",
                        "class_identifier": "5A",
                        "scholar_public_ids": [self.scholar_public_id],
                        "active_topic_key": "topic",
                        "title": "Topic",
                        "status": "INITIATED",
                        "period_date": self.local_today.isoformat(),
                        "initiated_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_standalone_exports_are_tenant_scoped_and_include_only_compiled_sources(self) -> None:
        now = datetime.now(timezone.utc)
        layout = {
            "layoutMode": "grid",
            "gridColumns": 2,
            "gridRows": 1,
            "gap": 12,
            "canvasHeight": 480,
            "canvasLayout": [],
            "subTabs": [
                {
                    "tabId": "export-tab",
                    "tabLabelDisplayName": "Export page",
                    "displayOrderWeight": 0,
                    "status": "active",
                    "layoutMode": "grid",
                    "gridColumns": 2,
                    "gridRows": 1,
                    "gap": 12,
                    "canvasHeight": 480,
                    "canvasLayout": [
                        {
                            "id": "export-card",
                            "type": "text_observation_block",
                            "label": "Student overview",
                            "content": "Independent project content",
                            "dataBinding": "PRIVATE_PROFILE_MARKER",
                            "variableBinding": "CURRENT_PROFILE_NAME",
                            "functionBinding": "TRIGGER_OTP_GATE",
                            "visibility": {"master_admin": True},
                            "gridColumn": 1,
                            "gridRow": 1,
                            "gridColumnSpan": 1,
                            "gridRowSpan": 1,
                            "x": 5,
                            "y": 8,
                            "width": 40,
                            "height": 24,
                            "styles": {
                                "color": "#123456",
                                "backgroundColor": "#ABCDEF",
                            },
                        }
                    ],
                }
            ],
            "activeSubTabId": "export-tab",
        }
        website_blueprint = {
            "schemaVersion": 1,
            "templateType": "landing-saas",
            "sections": [
                {
                    "id": "hero",
                    "semanticTag": "section",
                    "label": "Landing hero",
                    "columns": 1,
                    "styles": {
                        "color": "#302B27",
                        "backgroundColor": "#FFFFFF",
                    },
                    "children": [
                        {
                            "id": "hero-heading",
                            "type": "heading2",
                            "x": 64,
                            "y": 96,
                            "width": 360,
                            "height": 220,
                            "data": {"content": "A standalone website"},
                            "componentType": "heading2",
                            "semanticTag": "h2",
                            "label": "Hero heading",
                            "content": "A standalone website",
                            "styles": {
                                "color": "#302B27",
                                "backgroundColor": "#FFFFFF",
                            },
                        },
                        {
                            "id": "contact-cta",
                            "componentType": "cta_button",
                            "semanticTag": "button",
                            "label": "Contact us",
                            "content": "Contact us",
                            "actionBinding": "SCROLL_TO_CONTACT_FORM",
                            "styles": {
                                "color": "#302B27",
                                "backgroundColor": "#FFFFFF",
                            },
                        },
                    ],
                }
            ],
        }
        with Session(self.engine) as session:
            foreign_tenant = SchoolTenant(
                name="Other Export School",
                slug="other-export-school",
            )
            session.add(foreign_tenant)
            session.flush()
            session.add_all(
                [
                    LowCodeUserProjectSnapshot(
                        project_id="active-canvas-export",
                        project_title="Independent Canvas",
                        selected_base_layout_type="custom",
                        canvas_blueprint_data={"layout": layout},
                        created_timestamp=now,
                        tenant_id=self.tenant_id,
                    ),
                    LowCodeUserProjectSnapshot(
                        project_id="foreign-canvas-export",
                        project_title="Foreign Canvas",
                        selected_base_layout_type="custom",
                        canvas_blueprint_data={"layout": layout},
                        created_timestamp=now,
                        tenant_id=foreign_tenant.id,
                    ),
                    LowCodeWebsiteSnapshot(
                        site_id="active-website-export",
                        site_title="Independent Website",
                        selected_web_template_type="landing-saas",
                        semantic_blueprint_data=website_blueprint,
                        created_timestamp=now,
                        tenant_id=self.tenant_id,
                    ),
                ]
            )
            session.commit()

        update_response = self.client("master_admin").put(
            "/api/studio/projects/update/active-canvas-export",
            json={
                "projectTitle": "Updated Canvas Export",
                "selectedBaseLayoutType": "custom",
                "canvasBlueprintData": {"layout": layout},
            },
        )
        self.assertEqual(update_response.status_code, 200, update_response.text)
        self.assertEqual(update_response.json()["projectTitle"], "Updated Canvas Export")

        canvas_response = self.client("master_admin").get(
            "/api/studio/export/standalone/active-canvas-export",
            params={"collection": "canvas-projects"},
        )
        self.assertEqual(canvas_response.status_code, 200, canvas_response.text)
        self.assertEqual(canvas_response.headers["content-type"], "application/zip")
        self.assertIn(
            'filename="fieldnotes_exported_app.zip"',
            canvas_response.headers["content-disposition"],
        )
        with ZipFile(BytesIO(canvas_response.content)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "fieldnotes_exported_app/package.json",
                    "fieldnotes_exported_app/index.html",
                    "fieldnotes_exported_app/app_blueprint.js",
                    "fieldnotes_exported_app/runtime_renderer.js",
                },
            )
            files = {
                name: archive.read(name).decode("utf-8")
                for name in archive.namelist()
            }
        self.assertIn("Independent project content", files["fieldnotes_exported_app/app_blueprint.js"])
        self.assertIn("TRIGGER_OTP_GATE", files["fieldnotes_exported_app/app_blueprint.js"])
        self.assertIn("#123456", files["fieldnotes_exported_app/app_blueprint.js"])
        self.assertNotIn("PRIVATE_PROFILE_MARKER", str(files))
        self.assertNotIn("CURRENT_PROFILE_NAME", str(files))
        self.assertIn("vite", files["fieldnotes_exported_app/package.json"])

        website_response = self.client("master_admin").get(
            "/api/studio/export/standalone/active-website-export",
            params={"collection": "semantic-websites"},
        )
        self.assertEqual(website_response.status_code, 200, website_response.text)
        with ZipFile(BytesIO(website_response.content)) as archive:
            website_source = archive.read(
                "fieldnotes_exported_app/app_blueprint.js"
            ).decode("utf-8")
            website_renderer = archive.read(
                "fieldnotes_exported_app/runtime_renderer.js"
            ).decode("utf-8")
        self.assertIn("A standalone website", website_source)
        self.assertIn("SCROLL_TO_CONTACT_FORM", website_source)
        self.assertIn(
            r'\"type\":\"heading2\",\"x\":64,\"y\":96,\"width\":360,\"height\":220',
            website_source,
        )
        self.assertIn(
            r'\"data\":{\"content\":\"A standalone website\"}',
            website_source,
        )
        self.assertIn('node.style.position = "absolute"', website_renderer)

        for workspace_id, expected_title in (
            ("scholar", "SCHOLAR PSYCHOMETRICS"),
            ("healthcare", "HEALTHCARE MANAGEMENT"),
        ):
            core_response = self.client("master_admin").get(
                f"/api/studio/export/standalone/{workspace_id}",
                params={"collection": "core-workspaces"},
            )
            self.assertEqual(core_response.status_code, 200, core_response.text)
            with ZipFile(BytesIO(core_response.content)) as archive:
                core_source = archive.read(
                    "fieldnotes_exported_app/app_blueprint.js"
                ).decode("utf-8")
            self.assertIn(expected_title, core_source)
            self.assertNotIn("tenant_id", core_source)
            self.assertNotIn("profile", core_source.lower())

        unknown_core_response = self.client("master_admin").get(
            "/api/studio/export/standalone/unknown-core",
            params={"collection": "core-workspaces"},
        )
        self.assertEqual(unknown_core_response.status_code, 404)

        foreign_response = self.client("master_admin").get(
            "/api/studio/export/standalone/foreign-canvas-export",
            params={"collection": "canvas-projects"},
        )
        self.assertEqual(foreign_response.status_code, 404)
        denied = self.client("principal").get(
            "/api/studio/export/standalone/active-canvas-export",
            params={"collection": "canvas-projects"},
        )
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()