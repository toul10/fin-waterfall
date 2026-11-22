"""Optional server-side API transports for drafting from one reviewed excerpt."""
from http.client import HTTPSConnection, HTTPException
import json
import os

PROVIDERS = {
    "openai": ("OpenAI", "OPENAI_API_KEY", "OPENAI_DRAFT_MODEL"),
    "anthropic": ("Claude", "ANTHROPIC_API_KEY", "ANTHROPIC_DRAFT_MODEL"),
}


def choices(local_enabled):
    result = [{"id": "local", "label": "Local Qwen", "available": local_enabled}]
    for name, (label, key_env, model_env) in PROVIDERS.items():
        result.append({"id": name, "label": label,
                       "available": bool(os.environ.get(key_env) and os.environ.get(model_env))})
    return result


def model_name(provider):
    if provider not in PROVIDERS:
        raise ValueError("Choose a configured drafting provider.")
    label, key_env, model_env = PROVIDERS[provider]
    if not os.environ.get(key_env) or not os.environ.get(model_env):
        raise ValueError(f"To draft with {label}, configure {key_env} and {model_env} at startup.")
    return os.environ[model_env]


def proposal(provider, model, instructions, excerpt, schema, max_tokens=1024, timeout=45):
    label, key_env, _ = PROVIDERS[provider]
    headers = {"Content-Type": "application/json"}
    if provider == "openai":
        host, path = "api.openai.com", "/v1/responses"
        headers["Authorization"] = "Bearer " + os.environ[key_env]
        body = {"model": model, "input": excerpt, "instructions": instructions, "store": False,
                "text": {"format": {"type": "json_schema", "name": "payment_rule",
                                    "strict": True, "schema": schema}}}
    else:
        host, path = "api.anthropic.com", "/v1/messages"
        headers.update({"x-api-key": os.environ[key_env], "anthropic-version": "2023-06-01"})
        body = {"model": model, "max_tokens": max_tokens, "system": instructions,
                "messages": [{"role": "user", "content": excerpt}],
                "output_config": {"format": {"type": "json_schema", "schema": schema}}}

    connection = HTTPSConnection(host, timeout=timeout)
    try:
        connection.request("POST", path, json.dumps(body), headers)
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"{label} returned HTTP {response.status}. Check the API key, model access and quota.")
        raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError(f"{label} returned an oversized draft. Try a shorter excerpt.")
        data = json.loads(raw)
        if provider == "openai":
            if data.get("status") != "completed":
                raise ValueError(f"{label} did not finish the draft. Review the excerpt and try again.")
            parts = [part["text"] for output in data["output"] if output.get("type") == "message"
                     for part in output["content"] if part.get("type") == "output_text"]
        else:
            if data.get("stop_reason") != "end_turn":
                raise ValueError(f"{label} did not finish the draft. Review the excerpt and try again.")
            parts = [part["text"] for part in data["content"] if part.get("type") == "text"]
        parsed = json.loads("".join(parts))
        if not isinstance(parsed, dict) or set(parsed) != set(schema["required"]):
            raise ValueError(f"{label} returned an incomplete payment rule. Review the selected clause.")
        return parsed
    except (OSError, HTTPException) as error:
        raise ValueError(f"Could not reach {label}. Check network access or use Local Qwen.") from error
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"{label} did not return a usable draft. Check the excerpt and model selection.") from error
    finally:
        connection.close()
