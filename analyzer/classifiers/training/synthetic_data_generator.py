"""
synthetic_data_generator.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Generates labeled (LogSignal, FailureCategory) training pairs for the
SemanticClassifier by expanding regex patterns into realistic log sentences.

Why this approach
-----------------
The semantic model needs text diversity to generalise — if we just feed it
the raw regex strings it will overfit to regex syntax rather than log language.
We therefore:

  1. Build a vocabulary of realistic template sentences per category, drawn
     from the actual log samples and common CI/CD log phrasing.
  2. Expand each template with light variation (different tool names, paths,
     counts) so the TF-IDF vector has spread.
  3. Assign each sample the correct FailureCategory label.

The generator is deterministic (fixed random seed) so the output is
reproducible across runs.  Call generate() once at deploy time and save
the result via SemanticClassifier.save().
"""

from __future__ import annotations

import logging
import random
from typing import List, Tuple

from api.schemas.failure_category_schema import FailureCategory
from api.schemas.log_signal_schema import LogSignal

logger = logging.getLogger(__name__)

SEED = 42

_TEMPLATES: dict[FailureCategory, list[tuple[str, str | None]]] = {

    # ── Developer failures ─────────────────────────────────────────────────
    FailureCategory.DEV_FAILURE: [
        ("error: compilation failed in module {module}", "make: *** [Makefile:{line}] Error 1"),
        ("fatal error: {header}.h: No such file or directory", "compilation terminated.\nbuild failed"),
        ("undefined reference to `{symbol}'", "collect2: error: ld returned 1 exit status"),
        ("clang: error: linker command failed with exit code 1", "ld: can't open output file for writing"),
        ("gcc: error: {file}.o: No such file or directory", "make: *** [all] Error 1"),
        ("syntax error near unexpected token `{token}'", "build failed"),
        ("undefined symbol: {symbol}", "linker error\nmingw32-make: *** [Makefile:{line}] Error 2"),
        ("gradle build failed", "FAILURE: Build failed with an exception.\nCompilation error"),
        ("maven build failed", "BUILD FAILURE\n[ERROR] compilation failed"),
        ("compilation terminated. fatal error: {header}.h not found", "make failed"),
        ("make: *** [{target}] Error {code}", "build failed due to compilation error"),
        ("preprocessor: error expanding macro {macro}", "compilation failed"),
        # Dependencies
        ("could not resolve dependency {module}", "dependency resolution failed"),
        ("ERROR: Could not find a version that satisfies the requirement {module}", "No matching distribution found"),
        ("npm ERR! 404 Not Found: {module}", "npm error: package not found"),
        ("Module not found: Error: Can't resolve '{module}'", "dependency missing"),
        ("Could not find artifact {module}:{file}", "dependency resolution failed\nunresolved dependency"),
        ("version conflict: {module} requires {file} but found {header}", "incompatible dependency"),
        ("Library not found for: -{module}", "linker error: missing dependency"),
        # Code quality
        ("ERROR QUALITY GATE STATUS: FAILED", "quality gate failed\ncoverage is less than {pct}%"),
        ("quality gate status: failed", "new code coverage {pct}% is less than {pct2}%\nEXECUTION FAILURE"),
        ("failed quality gate", "coverage is less than required threshold\nquality gate condition failed"),
        ("quality gate failed: new code has {count} blocker issues", "quality gate status failed"),
        ("sonarscanner failed to connect to sonarqube server", "sonar authentication failed\nhttp 401"),
        ("linting failed: {count} errors found", "static analysis failed\nlint error"),
        ("pending identifications found in scan", "files pending for identification\nfail build on pending"),
    ],

    # ── Test failures ──────────────────────────────────────────────────────
    FailureCategory.TEST_FAILURE: [
        ("{count} tests failed", "junit: test execution failed\n{count} failures, {count2} errors"),
        ("assertion failed: expected {val} but was {val2}", "test execution failed"),
        ("pytest: {count} failed, {count2} passed", "FAILED {file}::test_{func}"),
        ("testng: {count} tests failed out of {count2}", "test suite failed"),
        ("java.lang.AssertionError: expected:<{val}> but was:<{val2}>", "junit test failure"),
        ("tests failed: {count} failures in {file}", "test execution failed\nassert {val} == {val2}"),
        ("test execution failed with exit code {code}", "{count} tests failed\n{count2} errors"),
        ("AssertionError: {count} != {count2}", "pytest test failure\ncollected {count} items"),
    ],

    # ── CI/CD + Infrastructure failures ────────────────────────────────────
    FailureCategory.CI_INFRA_FAILURE: [
        # Pipeline / workflow
        ("WorkflowScript: {line}: unexpected token: {token}",
         "org.jenkinsci.plugins.workflow.cps.CpsCompilationErrorsException"),
        ("WorkflowScript: {line}: expecting '}}', found '{token}'",
         "groovy compilation error\npipeline failed"),
        ("No such DSL method '{func}' found among steps",
         "pipeline script error\nworkflow execution failed"),
        ("YAML syntax error: mapping values are not allowed here at line {line}",
         "workflow syntax error\ninvalid pipeline configuration"),
        ("Invalid workflow file: .github/workflows/{pipeline_file}",
         "workflow syntax error\npipeline failed"),
        ("Failed to parse pipeline script",
         "pipeline configuration invalid\nfailed to load workflow"),
        ("No stages defined in pipeline",
         "pipeline configuration invalid"),
        # Env / config / credentials
        ("environment variable {var} is not set", "required configuration missing"),
        ("secret {var} not found", "credentials not configured"),
        ("token not set: {var}", "authentication failed\napi key missing"),
        ("ERROR: authentication failed — check {var}", "unauthorized: 401\ncredential not found"),
        ("configuration file not found: {file}.yml", "config error\nfailed to load settings"),
        ("permission denied accessing secret {var}", "http 403 forbidden\naccess rights missing"),
        # Artifacts / registry
        ("No artifacts found that match the file pattern \"{path}/*.{ext}\". Configuration error?",
         "{path}/*.{ext} doesnt match anything\n0 artifacts archived"),
        ("0 artifacts archived", "no artifacts found that match file pattern"),
        ("failed to publish artifact to artifactory", "upload failed\nhttp 500"),
        ("docker push failed: http 500 internal server error", "registry upload failed"),
        ("maven deploy failed: could not transfer artifact", "upload failed"),
        # Docker / container
        ("docker build failed at step RUN {func}", "error response from daemon\nbuild failed"),
        ("pull access denied for {file}", "image not found\nmanifest unknown"),
        ("failed to pull image {file}:{code}", "cannot pull image\ndocker error"),
        ("container exited with non-zero code {code}", "docker run failed\noci runtime error"),
        ("cannot connect to the Docker daemon at unix:///var/run/docker.sock", "docker daemon error"),
        ("Dockerfile error at line {line}", "build failed\ninvalid Dockerfile instruction"),
        # Kubernetes / orchestration
        ("pod {file} is in CrashLoopBackOff", "back-off restarting failed container"),
        ("ImagePullBackOff for image {file}", "ErrImagePull\nfailed to pull image"),
        ("pod scheduling failed: insufficient memory", "unschedulable\nno nodes available"),
        ("OOMKilled: container {module} exceeded memory limit", "pod evicted\nresource exhausted"),
        ("kubectl error: deployment {module} failed", "rollout failed\ndeployment error"),
        ("ConfigMap {var} not found in namespace {module}", "configuration error\nkubectl error"),
        ("failed to mount volume {path}", "container failed to start\nkubernetes error"),
        # Network / connectivity
        ("connection refused: {host}:{port}", "network error\ncannot connect to service"),
        ("connection timed out: {host}:{port}", "socket timeout\nfailed to connect"),
        ("dns resolution failed for {host}", "name or service not known\nnetwork error"),
        ("no route to host: {host}", "host unreachable\nnetwork failure"),
        ("http 503 service unavailable from {host}", "network error\nserver unreachable"),
        # Resources (OOM, disk, CPU)
        ("out of memory: kill process {pid}", "OOM killer activated\nno space left on device"),
        ("no space left on device", "disk quota exceeded\nout of memory"),
        ("java.lang.OutOfMemoryError: Java heap space", "GC overhead limit exceeded"),
        ("cpu limit exceeded for container {module}", "resource constraints\nprocess throttled"),
        # Agent / runner / executor
        ("node is offline", "jenkins agent terminated\ncannot contact node"),
        ("executor lost: agent disconnected", "connection to agent lost\nchannel closed"),
        ("runner offline: {host}", "runner has stopped\ninfra failure"),
        ("[InfluxDB Plugin] Ignoring Exception", "infra warning\nobservability failure"),
    ],
}

_VOCAB: dict[str, list[str]] = {
    "module":        ["core", "utils", "auth", "api", "sensor", "driver", "hal", "net"],
    "header":        ["config", "types", "platform", "sensor_api", "bmi3x0", "hal"],
    "symbol":        ["_init_sensor", "bmi3x0_init", "hal_read", "spi_transfer", "gpio_set"],
    "file":          ["main", "sensor", "driver", "utils", "config", "api_handler"],
    "token":         ["then", "do", "fi", "done", "{", "("],
    "target":        ["all", "build", "test", "clean", "install"],
    "macro":         ["SENSOR_ENABLE", "HAL_VERSION", "CONFIG_DEBUG", "PLATFORM_ID"],
    "func":          ["test_init", "test_read", "test_config", "test_connection", "test_parse"],
    "val":           ["true", "0", "null", "200", "SUCCESS", "1"],
    "val2":          ["false", "1", "ERROR", "404", "FAIL", "0"],
    "pct":           ["45", "62", "71", "38", "55", "80"],
    "pct2":          ["80", "75", "90", "85", "70"],
    "count":         ["3", "7", "12", "1", "5", "23"],
    "count2":        ["1", "2", "4", "0"],
    "line":          ["42", "108", "237", "15", "89"],
    "code":          ["1", "2", "127", "255"],
    "path":          ["linter_results", "unit-tests", "build/output", "dist", "reports"],
    "ext":           ["lint", "zip", "json", "xml", "html", "log"],
    "host":          ["sonarqube.internal", "fossid.company.com", "artifactory.corp"],
    "port":          ["8080", "443", "9000", "8443"],
    "pid":           ["1234", "5678", "9012"],
    "var":           ["SONAR_AUTH_TOKEN", "FOSSID_API_KEY", "ARTIFACTORY_TOKEN", "KUBECONFIG", "DOCKER_TOKEN"],
    "pipeline_file": ["Jenkinsfile", "build.yml", "main.yml", "pipeline.groovy", "ci.yml"],
}


def _expand(template: str, rng: random.Random) -> str:
    """Replace {var} placeholders with random vocabulary items."""
    """
    Substitute all ``{key}`` placeholders in *template* with random vocabulary entries.

    Args:
        template: Template string with ``{key}`` placeholders.
        rng:      Seeded random instance for reproducibility.

    Returns:
        Expanded string with all placeholders replaced.
    """
    result = template
    for key, choices in _VOCAB.items():
        placeholder = f"{{{key}}}"
        while placeholder in result:
            result = result.replace(placeholder, rng.choice(choices), 1)
    return result


def _make_signal(error_line: str, post_content: str | None, stage: str) -> LogSignal:
    """
    Build a minimal :class:`LogSignal` from raw log fragments.

    Args:
        error_line:   The primary error line extracted from the log.
        post_content: Lines following the error line (post-context).
        stage:        Name of the CI stage in which the error occurred.

    Returns:
        A :class:`LogSignal` with an empty fingerprint (suitable for training only).
    """
    return LogSignal(
        stage=stage,
        signal_type="ERROR",
        fingerprint="",
        error_line=error_line,
        post_content=post_content or "",
        pre_content="",
    )


_STAGE_FOR_CATEGORY: dict[FailureCategory, str] = {
    FailureCategory.DEV_FAILURE:      "build",
    FailureCategory.TEST_FAILURE:     "unit_test",
    FailureCategory.CI_INFRA_FAILURE: "pipeline",
}

# How many expanded samples to generate per template
SAMPLES_PER_TEMPLATE = 8


def generate(
    samples_per_template: int = SAMPLES_PER_TEMPLATE,
    seed: int = SEED,
) -> List[Tuple[LogSignal, FailureCategory]]:
    """
    Generate synthetic labeled training pairs.

    Returns a list of (LogSignal, FailureCategory) ready to pass directly
    into SemanticClassifier.train().

    Args:
        samples_per_template: How many expanded variants to generate per template.
                              Higher = better generalisation, slower training.
        seed: Random seed for reproducibility.
    """
    rng = random.Random(seed)
    dataset: List[Tuple[LogSignal, FailureCategory]] = []

    for category, templates in _TEMPLATES.items():
        stage = _STAGE_FOR_CATEGORY.get(category, "unknown_stage")

        for error_tmpl, post_tmpl in templates:
            for _ in range(samples_per_template):
                error_line   = _expand(error_tmpl, rng)
                post_content = _expand(post_tmpl, rng) if post_tmpl else None
                signal       = _make_signal(error_line, post_content, stage)
                dataset.append((signal, category))

    rng.shuffle(dataset)

    for i, item in enumerate(dataset):
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(
                f"generate() produced a malformed entry at index {i}: "
                f"expected (LogSignal, FailureCategory) 2-tuple, "
                f"got {type(item).__name__} with length "
                f"{len(item) if hasattr(item, '__len__') else 'unknown'}. "
                f"Check _TEMPLATES for entries with != 2 elements."
            )

    return dataset


def generate_and_save(
    model_path: str = "models/semantic.pkl",
    samples_per_template: int = SAMPLES_PER_TEMPLATE,
    seed: int = SEED,
) -> None:
    """
    Generate synthetic training data, train the SemanticClassifier and persist it.

    This is the recommended entry point for bootstrapping a fresh model or
    regenerating training data after template changes.

    Args:
        model_path:           Path where the classifier pickle and FAISS index
                              will be written.
        samples_per_template: Samples to generate per template row.
        seed:                 Random seed passed through to :func:`generate`.
    """
    from analyzer.classifiers.semantic_classifier import SemanticClassifier

    dataset = generate(samples_per_template=samples_per_template, seed=seed)

    logger.info(
        "Generated %d synthetic training samples across %d categories.",
        len(dataset),
        len(_TEMPLATES),
    )

    clf = SemanticClassifier(model_path=model_path)
    clf.train(dataset)
    clf.save()

    from collections import Counter
    counts = Counter(cat.value for _, cat in dataset)

    logger.info("Samples per category:")
    for cat, n in sorted(counts.items()):
        logger.info("  %-35s %d", cat, n)