import textwrap

from api.schemas.classified_schema import ClassifiedSignal
from api.schemas.failure_category_schema import FailureCategory

CATEGORY_ACTION_MAP: dict[FailureCategory, str] = {
    FailureCategory.DEV_FAILURE:      "FIX_DEV",
    FailureCategory.TEST_FAILURE:     "FIX_TEST",
    FailureCategory.CI_INFRA_FAILURE: "FIX_CI_INFRA",
    FailureCategory.UNKNOWN:          "MANUAL_INVESTIGATION",
}

ACTION_STRATEGY_MAP: dict[str, list[str]] = {
    "FIX_DEV": [
        "CLEAN_REBUILD",           # Re-run the build with clean state
        "RESOLVE_DEPENDENCY",      # Fix unresolvable/missing dependency
        "UPGRADE_DEPENDENCY",      # Bump dependency to a compatible version
        "GENERATE_CODE_PATCH",     # Produce a unified diff to fix source code
        "APPLY_LINT_FIX",          # Run formatter/linter to auto-fix style errors
        "FIX_CODE_QUALITY",        # Improve coverage, fix blocker issues, pending scans
        "REVERT_COMMIT",           # Revert the breaking commit
    ],
    "FIX_TEST": [
        "FIX_ASSERTION",           # Correct wrong expected value in assertion
        "RESET_TEST_DATA",         # Restore test fixtures/database to clean state
        "UPDATE_TEST_SNAPSHOT",    # Regenerate snapshot baselines
        "INCREASE_TEST_TIMEOUT",   # Widen timeout for slow tests
        "MOCK_EXTERNAL_SERVICE",   # Stub out a dependency the test hits for real
        "MARK_FLAKY_RETRY",        # Add retry annotation to a known-flaky test
        "SKIP_TEST_TEMPORARILY",   # Annotate skip with ticket ref (last resort)
    ],
    "FIX_CI_INFRA": [
        "FIX_PIPELINE_SYNTAX",     # Correct Jenkinsfile / workflow YAML syntax
        "UPDATE_ENV_VAR",          # Set or correct a missing/wrong env variable
        "ROTATE_CREDENTIALS",      # Rotate expired or revoked secret/token/key
        "UPDATE_SECRET",           # Update secret value in vault/k8s (mask with ***)
        "FIX_PATH_OR_PATTERN",     # Correct a wrong file glob or path in CI config
        "ENSURE_ARTIFACT_GENERATION", # Add/fix the build step that produces the artifact
        "FIX_CONTAINER_IMAGE",     # Fix image tag, registry auth, or Dockerfile error
        "FIX_K8S_MANIFEST",        # Fix pod spec, resource limits, probe, or volume mount
        "RESTART_RESOURCE",        # Restart a pod, container, agent, or service
        "REPROVISION_RUNNER",      # Tear down and re-register a CI runner/agent
        "RETRY_WITH_CLEAN_CACHE",  # Bust the build/dependency cache and retry
        "SCALE_RESOURCES",         # Increase memory/CPU limits to prevent OOM/throttle
        "FIX_NETWORK_POLICY",      # Update firewall rule, network policy, or DNS config
        "UPDATE_PLUGIN_VERSION",   # Update a Jenkins/CI plugin to a fixed version
        "FIX_PLUGIN_CONFIGURATION",# Correct a plugin config key/value
        "VALIDATE_FILE_EXISTENCE", # Verify and restore a missing file or volume
    ],
    "MANUAL_INVESTIGATION": [],
}


# ─────────────────────────────────────────────────────────────────────────────
# Per-action field contracts — what the LLM MUST populate for each action type
# ─────────────────────────────────────────────────────────────────────────────
FIX_DEV_FIELD_CONTRACT = """
REQUIRED FIELDS FOR FIX_DEV
────────────────────────────
- `target`        : Exact filename or path that must be changed (e.g. "Makefile", "pom.xml").
- `related_files` : Every other file touched or relevant to the fix. Use [] only if truly none.

STRATEGY-SPECIFIC REQUIREMENTS
- CLEAN_REBUILD       → `fix_commands`: ["mvn clean install -U"] or build-tool equivalent.
- RESOLVE_DEPENDENCY  → `related_files`: dependency manifests (pom.xml, package.json, requirements.txt).
                        `fix_commands`: exact shell commands to resolve.
- UPGRADE_DEPENDENCY  → `fix_commands`: package manager upgrade with exact version from log evidence.
- GENERATE_CODE_PATCH → `suggested_patch` REQUIRED (unified diff). `patch_confidence` REQUIRED (0.0–1.0).
- APPLY_LINT_FIX      → `fix_commands`: exact lint/format command + target file.
- FIX_CODE_QUALITY    → `fix_commands`: commands to raise coverage, clear blocker issues, or resolve
                        pending scan findings (e.g. sonar re-scan, fossid approval command).
- REVERT_COMMIT       → `fix_commands`: git revert command using the exact commit SHA visible in the log.

DO NOT use placeholders like <version> or <sha> — extract the exact value from the log evidence.
"""

FIX_TEST_FIELD_CONTRACT = """
REQUIRED FIELDS FOR FIX_TEST
──────────────────────────────
- `target`       : Exact test ID, test file path, or test class name.
- `fix_commands` : Shell-executable commands to apply the fix.

STRATEGY-SPECIFIC REQUIREMENTS
- MARK_FLAKY_RETRY      → `retry_count` REQUIRED (1–5). `fix_commands`: CI config patch to add retry annotation.
- RESET_TEST_DATA       → `fix_commands`: SQL/script to reset fixture data.
- UPDATE_TEST_SNAPSHOT  → `fix_commands`: ["pytest --snapshot-update <test_path>"] or equivalent.
- INCREASE_TEST_TIMEOUT → `fix_commands`: exact timeout config change.
- SKIP_TEST_TEMPORARILY → `skip_reason` REQUIRED (include ticket ref or justification).
                          `fix_commands`: annotation or skip decorator to add.
- FIX_ASSERTION         → `fix_commands`: show the corrected assert statement.
- MOCK_EXTERNAL_SERVICE → `fix_commands`: mock registration or fixture code snippet.
"""

FIX_CI_INFRA_FIELD_CONTRACT = """
REQUIRED FIELDS FOR FIX_CI_INFRA
──────────────────────────────────
- `target`                         : Exact resource — runner name, secret key, image tag, manifest path, plugin ID.
- `estimated_recovery_time_seconds`: Integer estimate of recovery time.
- `requires_human_approval`        : true if the fix touches secrets, prod infra, billing, or live deployments.
- `fix_commands`                   : Shell or CI-config commands to apply the fix verbatim.

STRATEGY-SPECIFIC REQUIREMENTS
- FIX_PIPELINE_SYNTAX    → `fix_commands`: sed/patch command that corrects the exact syntax error in the
                           Jenkinsfile or workflow YAML, with the precise line from the log.
- UPDATE_ENV_VAR         → `fix_commands`: export or CI variable-set command with the exact var name.
- ROTATE_CREDENTIALS     → `fix_commands`: rotation CLI command. `requires_human_approval`: true.
- UPDATE_SECRET          → `fix_commands`: vault/k8s secret update (mask value with ***). `requires_human_approval`: true.
- FIX_PATH_OR_PATTERN    → `fix_commands`: sed/config-edit with the exact corrected path or glob.
- ENSURE_ARTIFACT_GENERATION → `fix_commands`: build step that produces the missing artifact.
- FIX_CONTAINER_IMAGE    → `fix_commands`: docker pull / tag / Dockerfile edit or registry login command.
- FIX_K8S_MANIFEST       → `fix_commands`: kubectl patch or manifest YAML edit (memory/CPU limits,
                           probe config, secret ref, volume mount).
- RESTART_RESOURCE       → `fix_commands`: ["kubectl rollout restart deployment/<name>"] or equivalent.
- REPROVISION_RUNNER     → `fix_commands`: runner teardown + re-registration steps.
- RETRY_WITH_CLEAN_CACHE → `fix_commands`: cache-bust command or CI cache-clear directive.
- SCALE_RESOURCES        → `fix_commands`: resource limit config change (YAML snippet or CLI).
- FIX_NETWORK_POLICY     → `fix_commands`: network policy / firewall rule change. `requires_human_approval`: true.
- UPDATE_PLUGIN_VERSION  → `fix_commands`: plugin manager command with target version.
- FIX_PLUGIN_CONFIGURATION → `fix_commands`: exact config key/value diff.
- VALIDATE_FILE_EXISTENCE → `fix_commands`: stat/ls check + copy/create command if missing.
"""

MANUAL_INVESTIGATION_FIELD_CONTRACT = """
REQUIRED FIELDS FOR MANUAL_INVESTIGATION
──────────────────────────────────────────
- `reason`              : Clear explanation of why automation cannot resolve this.
- `suggested_next_step` : First concrete action for the on-call engineer (specific, not generic).
- `escalation_team`     : Team name or channel (e.g. "platform-oncall", "#infra-alerts").
- `priority`            : One of LOW / MEDIUM / HIGH / CRITICAL.
- `investigation_links` : List any relevant dashboard, runbook, or Jira URLs if inferable from context.
                          Use [] if none can be inferred.
"""

ACTION_FIELD_CONTRACTS = {
    "FIX_DEV":              FIX_DEV_FIELD_CONTRACT,
    "FIX_TEST":             FIX_TEST_FIELD_CONTRACT,
    "FIX_CI_INFRA":         FIX_CI_INFRA_FIELD_CONTRACT,
    "MANUAL_INVESTIGATION": MANUAL_INVESTIGATION_FIELD_CONTRACT,
}


def build_rca_prompt(classified_signal: ClassifiedSignal) -> str:
    signal = classified_signal.signal
    expected_action = CATEGORY_ACTION_MAP.get(
        classified_signal.best_category,
        "MANUAL_INVESTIGATION"
    )
    allowed_strategies = ACTION_STRATEGY_MAP.get(expected_action, [])
    field_contract = ACTION_FIELD_CONTRACTS.get(expected_action, "")

    if allowed_strategies:
        strategy_list = "\n".join(f"    - {s}" for s in allowed_strategies)
        strategy_constraint = (
            f"- The `strategy` field MUST be exactly one of:\n{strategy_list}\n"
            f"  Choose the single most appropriate strategy based on the error evidence."
        )
    else:
        strategy_constraint = (
            "- No `strategy` field is required for MANUAL_INVESTIGATION.\n"
            "- Populate all required MANUAL_INVESTIGATION fields as specified in the field contract below."
        )

    # Truncate dynamic content to keep prompt within ~1 800 token budget:
    #   static template ≈ 900 t  |  field_contract ≈ 300 t  |  dynamic ≈ 250 t  |  strategy list ≈ 100 t
    error_line   = signal.error_line[:400]   if signal.error_line   else "(none)"
    pre_context  = signal.pre_content[-150:] if signal.pre_content  else "(none)"
    post_context = signal.post_content[:250] if signal.post_content else "(none)"

    return textwrap.dedent(f"""\
        You are a deterministic CI/CD Root Cause Analysis engine.

        OBJECTIVE
        ─────────
        Analyze the CI failure evidence below and produce a structured SignalRCA response with:
        1. A concise (1–3 sentence) technical `root_cause` naming the exact failure mechanism
           visible in the logs — no speculation beyond the evidence.
        2. A specific, atomic, FULLY EXECUTABLE remediation that directly resolves the cause —
           including all shell commands, config changes, and code patches needed without further
           human lookup.

        CORE REMEDIATION RULES
        ──────────────────────
        - The `action` field MUST be exactly: {expected_action}
        {strategy_constraint}
        - `target` must be the specific file path, package name, secret key, or test ID —
          never a generic placeholder like "the project".
        - `fix_commands` MUST be a non-empty list of verbatim shell-executable strings.
          Every command must be complete and runnable — no placeholders like <version>, <sha>, <name>.
          Derive all values from the log evidence. Include all prerequisite steps in order.
        - GENERATE_CODE_PATCH: populate `suggested_patch` (unified diff) and `patch_confidence` (0.0–1.0).
        - SKIP_TEST_TEMPORARILY: populate `skip_reason` with ticket ref or justification.
        - MARK_FLAKY_RETRY: populate `retry_count` (1–5).
        - Avoid vague advice ("check logs", "investigate further").
        - If unsafe (secrets, prod infra): set `requires_human_approval: true`, still provide commands.
        - `related_files`: populate with every file touched by the fix; [] only when truly none.

        {field_contract}
        CONFIDENCE GUIDANCE
        ───────────────────
        - `rca_confidence`: ≥0.80 clear fix | 0.50–0.79 probable | <0.50 prefer MANUAL_INVESTIGATION
        - `severity`: CRITICAL pipeline blocked | HIGH downstream affected | MEDIUM isolated | LOW minor

        BUILD CONTEXT
        ─────────────
        Stage:              {signal.stage}
        Validated Category: {classified_signal.best_category.value}
        Expected Action:    {expected_action}

        LOG EXCERPT
        ───────────
        Error Line:
        {error_line}

        Context Before:
        {pre_context}

        Context After:
        {post_context}

        STRICT RULES
        ────────────
        - `action` MUST be: {expected_action}
        - `strategy` MUST be one of: {', '.join(allowed_strategies) if allowed_strategies else 'N/A'}
        - Do NOT re-classify the category — it is already validated upstream.
        - Do NOT speculate beyond the log evidence.
        - `fix_commands` must be a non-empty list for all actions except MANUAL_INVESTIGATION.
        - Do NOT include a `fingerprint` field — injected by the system after generation.
        - Populate `secondary_remediations` if a clear follow-up action is needed alongside the primary.
        - Null is only acceptable for `suggested_patch`/`patch_confidence` when strategy ≠ GENERATE_CODE_PATCH.

        Respond with the SignalRCA structure only. No explanation outside the JSON.
    """)