from __future__ import annotations

import httpx
import pytest

from itools.core.config import JiraSettings
from itools.core.http import JiraClient, JiraAPIError
from itools.jira.models import read_workbook
from itools.jira.template import generate_template
from itools.jira.importer import run_import, ImportStateError, state_file_for
from itools.jira.service import discover_metadata, issue_payload, canonical_links


class FakeJira:
    def __init__(self):
        self.created = {}
        self.links = set()
        self.next_id = 100
        self.fail_once = False

    def handle(self, request: httpx.Request):
        method, path = request.method, request.url.path
        prefix = "/rest/api/2/"
        assert path.startswith(prefix), path
        endpoint = path[len(prefix):]
        def ok(data, status=200):
            return httpx.Response(status, json=data)
        if method == "GET":
            if endpoint == "serverInfo":
                return ok({"version": "9.12.0", "serverTitle": "Mock Jira"})
            if endpoint == "project/DEV":
                return ok({"key": "DEV", "issueTypes": [
                    {"id": "100", "name": "Epic"},
                    {"id": "101", "name": "Task"},
                    {"id": "102", "name": "Sub-task", "subtask": True},
                ]})
            if endpoint == "field":
                return ok([
                    {"id": "customfield_10001", "name": "Epic Link",
                     "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-link"}},
                    {"id": "customfield_10002", "name": "Epic Name",
                     "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-label"}},
                ])
            if endpoint == "issueLinkType":
                return ok({"issueLinkTypes": [
                    {"name": "Blocks", "outward": "blocks", "inward": "is blocked by"},
                    {"name": "Relates", "outward": "relates to", "inward": "relates to"},
                ]})
            if endpoint == "priority":
                return ok([{"name": "High"}, {"name": "Medium"}])
            if endpoint == "project/DEV/components":
                return ok([{"name": "Platform"}, {"name": "Jenkins"}, {"name": "Nexus"}])
            if endpoint.startswith("issue/"):
                key = endpoint.split("/", 1)[1]
                if key not in self.created:
                    return ok({"errorMessages": ["not found"]}, 404)
                if request.url.params.get("fields") == "issuelinks":
                    links = [
                        {"type": {"name": typ}, "outwardIssue": {"key": dst}}
                        for typ, src, dst in self.links if src == key
                    ]
                    return ok({"key": key, "fields": {"issuelinks": links}})
                return ok({"id": key})
        if method == "POST" and endpoint == "issue":
            if self.fail_once:
                self.fail_once = False
                return ok({"errorMessages": ["temporary problem"]}, 500)
            import json
            fields = json.loads(request.content)["fields"]
            kind = fields["issuetype"]["id"]
            if kind == "100":
                assert fields["customfield_10002"] == fields["summary"]
            elif kind == "101":
                assert fields["customfield_10001"] in self.created
            else:
                assert fields["parent"]["key"] in self.created
            key = f"DEV-{self.next_id}"
            self.next_id += 1
            self.created[key] = fields
            return ok({"key": key}, 201)
        if method == "POST" and endpoint == "issueLink":
            import json
            fields = json.loads(request.content)
            self.links.add((
                fields["type"]["name"],
                fields["outwardIssue"]["key"],
                fields["inwardIssue"]["key"],
            ))
            return httpx.Response(201)
        raise AssertionError((method, endpoint))


def setup(tmp_path):
    path = tmp_path / "plan.xlsx"
    generate_template(path, example=True)
    report = read_workbook(path)
    assert report.valid, report.errors
    jira = FakeJira()
    client = JiraClient(
        JiraSettings(url="https://jira.example.internal", auth="pat", token="test"),
        transport=httpx.MockTransport(jira.handle),
    )
    return path, report, jira, client


def test_import_hierarchy_links_and_resume(tmp_path):
    path, report, fake, client = setup(tmp_path)
    with client:
        preview = run_import(client, report, "DEV", dry_run=True)
        assert preview.dry_run
        assert fake.created == {}
        assert not state_file_for(path).exists()
        first = run_import(client, report, "DEV")
        assert first.issue_created_now == 6
        assert len(fake.created) == 6
        assert len(fake.links) == 3  # Inverse relationships deduplicated.
        assert state_file_for(path).exists()
        with pytest.raises(ImportStateError):
            run_import(client, report, "DEV")
        second = run_import(client, report, "DEV", resume=True)
        assert second.issue_created_now == 0
        assert second.issue_reused == 6
        assert second.links_created_now == 0
        assert len(fake.created) == 6


def test_resume_after_transient_failure(tmp_path):
    path, report, fake, client = setup(tmp_path)
    with client:
        # Fail before writing first issue (no state yet); a fresh run remains possible.
        fake.fail_once = True
        with pytest.raises(JiraAPIError):
            run_import(client, report, "DEV")
        assert not state_file_for(path).exists()
        result = run_import(client, report, "DEV")
        assert result.issue_created_now == 6


def test_fields_and_canonical_links(tmp_path):
    _, report, fake, client = setup(tmp_path)
    with client:
        metadata = discover_metadata(client, "DEV", report)
        root = next(i for i in report.issues if i.wbs == "1")
        payload = issue_payload(root, metadata, {})
        assert payload["customfield_10002"] == root.summary
        assert "parent" not in payload
        ids = {i.wbs: f"DEV-{100 + n}" for n, i in enumerate(report.issues)}
        links = canonical_links(report, metadata, ids)
        assert len(links) == 3


def test_detect_changed_imported_issue(tmp_path):
    path, report, fake, client = setup(tmp_path)
    with client:
        run_import(client, report, "DEV")
        from dataclasses import replace
        report.issues[0] = replace(report.issues[0], summary="Modified Epic")
        with pytest.raises(ImportStateError, match="planning fields changed"):
            run_import(client, report, "DEV", resume=True)
