"""Single-turn Vertex AI drafting. The only customer input is the confirmed text."""
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
import os
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Gemini:
    project: str
    location: str
    model: str
    fake_endpoint: str = ""

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]|[0-9]+", self.project):
            raise ValueError("Configure GOOGLE_CLOUD_PROJECT with the server's Google Cloud project ID.")
        if self.location not in ("global", "us", "eu") and not re.fullmatch(r"[a-z]+-[a-z]+[0-9]+", self.location):
            raise ValueError("Configure GOOGLE_CLOUD_LOCATION with a supported Vertex AI location.")
        if not re.fullmatch(r"gemini-[a-z0-9][a-z0-9.-]{0,100}", self.model):
            raise ValueError("Configure GEMINI_MODEL with the publisher's Gemini model ID.")
        if self.fake_endpoint:
            parsed = urlsplit(self.fake_endpoint)
            if (os.environ.get("K_SERVICE") or parsed.scheme != "http"
                    or parsed.hostname not in ("127.0.0.1", "localhost", "::1", "fake-gemini")
                    or not parsed.port or parsed.username is not None or parsed.password is not None
                    or parsed.path or parsed.query or parsed.fragment):
                raise ValueError("The fake Gemini endpoint must be a local test service with an explicit port.")
        if os.environ.get("K_SERVICE") and os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            raise ValueError("Attach a service identity to Cloud Run and remove GOOGLE_APPLICATION_CREDENTIALS.")

    @classmethod
    def from_env(cls):
        return cls(*(os.environ.get(k, "") for k in (
            "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GEMINI_MODEL", "GEMINI_FAKE_ENDPOINT")))

    @property
    def resource(self):
        return f"projects/{self.project}/locations/{self.location}/publishers/google/models/{self.model}"

    @property
    def api_host(self):
        if self.location in ("us", "eu"):
            return f"aiplatform.{self.location}.rep.googleapis.com"
        if self.location == "global":
            return "aiplatform.googleapis.com"
        return f"{self.location}-aiplatform.googleapis.com"

    @staticmethod
    def identity_headers():
        import google.auth
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport.requests import Request

        headers = {}
        try:
            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            credentials.refresh(Request())
            credentials.apply(headers)
        except GoogleAuthError as error:
            raise ValueError("Gemini could not authenticate. Ask the operator to check the service identity.") from error
        return headers

    def proposal(self, instructions, excerpt, schema, max_output_tokens=4096, timeout=45):
        system = ("Assign evidence labels P1, P2, and so on to successive paragraphs of the exact "
                  "user text. Blank lines separate paragraphs. A single wrapped line break does not. "
                  + instructions)
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": excerpt}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema,
                                     "maxOutputTokens": max_output_tokens}}
        headers = {"Content-Type": "application/json"}
        if self.fake_endpoint:
            target = urlsplit(self.fake_endpoint)
            connection = HTTPConnection(target.hostname, target.port, timeout=timeout)
        else:
            headers.update(self.identity_headers())
            connection = HTTPSConnection(self.api_host, timeout=timeout)
        try:
            connection.request("POST", f"/v1/{self.resource}:generateContent", json.dumps(body), headers)
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"Gemini returned HTTP {response.status}. Ask the operator to check "
                                 "the configured model, region, service access and quota if it persists.")
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("The response from Gemini exceeded the draft size limit.")
            candidate = json.loads(raw)["candidates"][0]
            if candidate.get("finishReason") != "STOP":
                raise ValueError("Gemini did not finish a draft. Review the selected excerpt.")
            parts = candidate["content"]["parts"]
            result = json.loads("".join(p["text"] for p in parts if not p.get("thought")))
            if not isinstance(result, dict) or set(result) != set(schema["required"]):
                raise ValueError("Gemini returned an incomplete rule. Review the selected excerpt.")
            return result
        except (OSError, HTTPException) as error:
            raise ValueError("Gemini could not be reached. Ask the operator to check connectivity if it persists.") from error
        except (IndexError, KeyError, TypeError, AttributeError, json.JSONDecodeError, UnicodeError) as error:
            raise ValueError("Gemini returned an unreadable draft. Review the selected excerpt.") from error
        finally:
            connection.close()
