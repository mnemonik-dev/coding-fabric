# fabric/molyanov/validators

Thin Python wrappers translating molyanov validator invocations into ruflo plugin
subprocess calls and normalising plugin output back to the molyanov finding schema.

## Wrappers

| Molyanov validator | ruflo plugin | Entry-point |
|--------------------|-------------|-------------|
| `skeptic` | `jujutsu` | `skeptic_wrapper.py` |
| `security-auditor` | `security-audit` | `security_auditor_wrapper.py` |
| `post-deploy-qa` | `browser` | `post_deploy_qa_wrapper.py` |

## Output schema

Every wrapper returns a dict:

```json
{
  "findings": [
    {
      "severity": "high",
      "area": "security",
      "file": "src/auth.py",
      "line": 42,
      "issue": "Hardcoded credential",
      "fix_recommendation": "Use environment variable"
    }
  ],
  "delegated_to": "ruflo:jujutsu"
}
```

## CLI usage

```bash
# skeptic
python -m fabric.molyanov.validators.skeptic_wrapper --target=src/
echo '{"target": "src/"}' | python -m fabric.molyanov.validators.skeptic_wrapper

# security auditor
python -m fabric.molyanov.validators.security_auditor_wrapper --target=src/ --profile=owasp

# post-deploy QA
python -m fabric.molyanov.validators.post_deploy_qa_wrapper --url=https://example.com --scenario=smoke

# dry-run (any wrapper)
python -m fabric.molyanov.validators.skeptic_wrapper --target=src/ --dry-run
```

## Running tests

```bash
pytest fabric/molyanov/validators/tests/ -v
```

## Design notes

- Each wrapper uses `subprocess.run` in argv-form (`shell=False`).
- Errors are logged via `fabric.logs.sanitizer.SanitizedStreamHandler` — no raw
  plugin stderr reaches disk unfiltered.
- `--dry-run` prints the intended invocation without executing ruflo.
- `_common.py` owns: severity mapping, field aliasing, JSON parsing, and the
  `invoke_ruflo` helper.
