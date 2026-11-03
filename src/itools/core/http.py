"""Small Jira Server/Data Center REST adapter. No automatic retries for writes."""

from __future__ import annotations

import httpx

from itools.core.config import JiraSettings


class JiraAPIError(RuntimeError):
    def __init__(self, method: str, endpoint: str, status: int | None, details: str):
        self.status = status
        self.method = method
        self.endpoint = endpoint
        super().__init__(f"{method} {endpoint} failed ({status or 'network'}): {details}")


class JiraClient:
    def __init__(self, settings: JiraSettings, *, transport: httpx.BaseTransport | None = None):
        headers = {"Accept": "application/json"}
        auth = None
        if settings.auth == "pat":
            headers["Authorization"] = f"Bearer {settings.token}"
        else:
            auth = httpx.BasicAuth(settings.username or "", settings.password or "")
        self.settings = settings
        self._client = httpx.Client(
            base_url=f"{settings.url}/rest/api/2/",
            headers=headers,
            auth=auth,
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            verify=settings.ca_bundle or True,
            transport=transport,
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._client.close()

    def request(self, method: str, endpoint: str, **kwargs):
        try:
            response = self._client.request(method, endpoint.lstrip("/"), **kwargs)
        except httpx.RequestError as exc:
            raise JiraAPIError(method, endpoint, None, str(exc)) from exc
        if response.is_error or response.is_redirect:
            try:
                body = response.json()
                if isinstance(body, dict):
                    details = "; ".join(map(str, body.get("errorMessages", [])))
                    details += ("; " if details else "") + "; ".join(
                        f"{k}: {v}" for k, v in body.get("errors", {}).items()
                    )
                    details = details or str(body)[:500]
                else:
                    details = str(body)[:500]
            except ValueError:
                details = response.text[:500]
            raise JiraAPIError(method, endpoint, response.status_code, details)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise JiraAPIError(method, endpoint, response.status_code, "Invalid JSON") from exc

    def get(self, endpoint: str, **kwargs):
        return self.request("GET", endpoint, **kwargs)

    def post(self, endpoint: str, **kwargs):
        return self.request("POST", endpoint, **kwargs)

    def server_info(self):
        return self.get("serverInfo")

    def project(self, project_key: str):
        return self.get(f"project/{project_key}")

    def fields(self):
        return self.get("field")

    def link_types(self):
        return self.get("issueLinkType").get("issueLinkTypes", [])

    def priorities(self):
        return self.get("priority")

    def components(self, project_key: str):
        return self.get(f"project/{project_key}/components")

    def create_component(self, project_key: str, name: str) -> dict:
        """Create a Jira project Component with the project default assignee policy."""
        result = self.post(
            "component",
            json={"project": project_key, "name": name, "assigneeType": "PROJECT_DEFAULT"},
        )
        if not result.get("id") or not result.get("name"):
            raise JiraAPIError("POST", "component", 200, "No Component ID/name returned")
        return result

    def get_issue(self, key: str, fields: str = "id"):
        return self.get(f"issue/{key}", params={"fields": fields})

    def create_issue(self, fields: dict) -> str:
        result = self.post("issue", json={"fields": fields})
        key = result.get("key")
        if not key:
            raise JiraAPIError("POST", "issue", 200, "No Jira issue key in response")
        return key

    def create_link(self, link_type: str, outward: str, inward: str):
        return self.post(
            "issueLink",
            json={
                "type": {"name": link_type},
                "outwardIssue": {"key": outward},
                "inwardIssue": {"key": inward},
            },
        )

    def has_link(self, link_type: str, outward: str, inward: str) -> bool:
        data = self.get_issue(outward, fields="issuelinks")
        for link in data.get("fields", {}).get("issuelinks", []):
            if (
                link.get("type", {}).get("name", "").casefold() == link_type.casefold()
                and link.get("outwardIssue", {}).get("key") == inward
            ):
                return True
        return False
