"""Jira metadata discovery and validation before any destructive API call."""

from __future__ import annotations

from dataclasses import dataclass

from itools.core.http import JiraClient
from itools.jira.models import PlanningIssue, ValidationReport, JIRA_KEY_RE


class PreflightError(ValueError):
    pass


@dataclass(frozen=True)
class Relation:
    type_name: str
    inward: bool
    symmetric: bool = False


@dataclass
class JiraMetadata:
    project: str
    issue_types: dict[str, dict]
    epic_link_field: str | None
    epic_name_field: str | None
    priority_names: set[str]
    component_names: set[str]
    relations: dict[str, Relation]

    def link(self, relationship: str) -> Relation:
        key = relationship.strip().casefold()
        if key not in self.relations:
            raise PreflightError(f"Unsupported Jira link relationship: {relationship!r}")
        return self.relations[key]


def _custom_field(fields: list[dict], name: str, schema_marker: str) -> str | None:
    matches = [
        entry.get("id") for entry in fields
        if schema_marker in str(entry.get("schema", {}).get("custom", "")).casefold()
    ]
    if not matches:
        matches = [
            entry.get("id") for entry in fields
            if entry.get("name", "").strip().casefold() == name.casefold()
        ]
    return matches[0] if len(matches) == 1 else None


def _issue_type_map(available: list[dict]) -> dict[str, dict]:
    result = {}
    for entry in available:
        name = entry.get("name", "").casefold().replace("-", "").replace(" ", "")
        if name == "epic":
            result["Epic"] = entry
        elif name == "task":
            result["Task"] = entry
        elif name == "subtask" or entry.get("subtask") and name in {"subtask", "subtasks"}:
            result["Sub-task"] = entry
    return result


def discover_metadata(
    client: JiraClient,
    project_key: str,
    report: ValidationReport,
) -> JiraMetadata:
    """Validate project, supported issue types and input values before creating issues."""
    if not report.valid:
        raise PreflightError("Excel validation failed; run 'itools jira issue validate' first")

    project = client.project(project_key)
    fields = client.fields()
    link_types = client.link_types()
    priority_entries = client.priorities()
    component_entries = client.components(project_key)
    types = _issue_type_map(project.get("issueTypes", []))
    errors: list[str] = []

    for issue in report.issues:
        if issue.issue_type not in types:
            errors.append(f"Row {issue.row}: {issue.issue_type} not enabled in Jira project {project_key}")

    epic_link = _custom_field(fields, "Epic Link", "gh-epic-link")
    epic_name = _custom_field(fields, "Epic Name", "gh-epic-label")
    if any(i.issue_type == "Task" for i in report.issues) and not epic_link:
        errors.append("Cannot discover a unique Epic Link custom field; use 'jira field list'")
    if any(i.issue_type == "Epic" for i in report.issues) and not epic_name:
        errors.append("Cannot discover a unique Epic Name custom field; use 'jira field list'")

    priorities = {p.get("name", "").casefold() for p in priority_entries}
    components = {c.get("name", "").casefold() for c in component_entries}
    for issue in report.issues:
        if issue.priority and issue.priority.casefold() not in priorities:
            errors.append(f"Row {issue.row}: priority {issue.priority!r} not found in Jira")
        for component in issue.components:
            if component.casefold() not in components:
                errors.append(f"Row {issue.row}: component {component!r} not found in project")

    relations: dict[str, Relation] = {}
    ambiguous = set()
    for item in link_types:
        name = item.get("name", "")
        outward = item.get("outward", "")
        inward = item.get("inward", "")
        symmetric = outward.casefold() == inward.casefold()
        for label, direction in ((outward, False), (inward, True)):
            key = label.casefold()
            if not key:
                continue
            # Symmetric relationships use the same outward/inward label.
            # Both directions are semantically equivalent; canonicalize them.
            rel = Relation(name, False if symmetric else direction, symmetric)
            if key in relations and relations[key] != rel:
                ambiguous.add(key)
            else:
                relations[key] = rel
    for key in ambiguous:
        relations.pop(key, None)
    for issue in report.issues:
        for link in issue.linked_issues:
            if link.relationship.casefold() not in relations:
                errors.append(
                    f"Row {issue.row}: relationship {link.relationship!r} "
                    "is not supported or is ambiguous on this Jira instance"
                )

    # Confirm existing Jira keys, so a missing external reference fails before any write.
    checked: set[str] = set()
    for issue in report.issues:
        for link in issue.linked_issues:
            key = link.target.upper()
            if JIRA_KEY_RE.fullmatch(key) and key not in checked:
                checked.add(key)
                try:
                    client.get_issue(key)
                except Exception as exc:
                    errors.append(f"Linked Jira issue {key} is unavailable: {exc}")

    if errors:
        raise PreflightError("\n".join(errors))
    return JiraMetadata(project_key, types, epic_link, epic_name, priorities, components, relations)


def issue_payload(
    issue: PlanningIssue, metadata: JiraMetadata, key_by_wbs: dict[str, str]
) -> dict:
    """Map the 11 planning columns to Jira REST API fields."""
    fields: dict = {
        "project": {"key": metadata.project},
        "issuetype": {"id": str(metadata.issue_types[issue.issue_type]["id"])},
        "summary": issue.summary,
    }
    if issue.description:
        fields["description"] = issue.description
    if issue.assignee:
        # Jira Server/DC username (not Cloud accountId).
        fields["assignee"] = {"name": issue.assignee}
    if issue.priority:
        fields["priority"] = {"name": issue.priority}
    if issue.components:
        fields["components"] = [{"name": name} for name in issue.components]
    if issue.labels:
        fields["labels"] = list(issue.labels)
    if issue.estimate:
        fields["timetracking"] = {"originalEstimate": issue.estimate}
    if issue.due_date:
        fields["duedate"] = issue.due_date

    if issue.issue_type == "Epic" and metadata.epic_name_field:
        # No separate Epic Name column: use Summary by design.
        fields[metadata.epic_name_field] = issue.summary[:255]
    elif issue.issue_type == "Task":
        fields[metadata.epic_link_field] = key_by_wbs[issue.parent_wbs]
    elif issue.issue_type == "Sub-task":
        fields["parent"] = {"key": key_by_wbs[issue.parent_wbs]}
    return fields


def canonical_links(
    report: ValidationReport, metadata: JiraMetadata, key_by_wbs: dict[str, str]
) -> list[tuple[str, str, str]]:
    result = []
    seen = set()
    for issue in report.issues:
        for link in issue.linked_issues:
            relation = metadata.link(link.relationship)
            src = key_by_wbs[issue.wbs]
            target = key_by_wbs.get(link.target, link.target.upper())
            outward, inward = (target, src) if relation.inward else (src, target)
            if outward == inward:
                raise PreflightError(f"Link points to the same issue: {outward}")
            if relation.symmetric:
                outward, inward = sorted((outward, inward))
            signature = (relation.type_name.casefold(), outward, inward)
            if signature not in seen:
                seen.add(signature)
                result.append((relation.type_name, outward, inward))
    return result
