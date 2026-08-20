# Worklog — item 8c: fix vault-guard env mismatch in alkyone.yml

## Change
`.github/workflows/alkyone.yml`: the job env now sets both
`HF_DATASET_ID` and `HF_DATASET_ID_TEST` to the same secret
(`secrets.ALKONE_TEST_VAULT`).

## Why
- The prod-safety guard (`alkyone/src/alkyone/guard.py:58`) reads
  `HF_DATASET_ID` directly from `os.environ` and runs as a separate process
  (`make guard` -> `python -m alkyone.guard`) that never imports
  `alkyone.fixtures`.
- Previously the workflow only set `HF_DATASET_ID_TEST`, so `HF_DATASET_ID`
  was unset in the guard process -> the vault check was a no-op.
- `alkyone/src/alkyone/fixtures.py:11` still force-sets `HF_DATASET_ID` from
  `HF_DATASET_ID_TEST` (or its default) at test import, so keeping
  `HF_DATASET_ID_TEST` set to the same value guarantees the guard validates the
  exact dataset the run uses.

## Files touched
- `.github/workflows/alkyone.yml` (only file edited)

## Validation
- `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/alkyone.yml')); print('OK')"` -> OK
- No real secrets introduced; existing secret references only.
- Workflow not triggered.
