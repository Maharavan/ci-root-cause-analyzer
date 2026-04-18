import json
import logging
from pathlib import Path
import re
from typing import List, Optional, Tuple
from api.app.config import settings
from api.schemas.signal_type_schema import SignalType
from api.schemas.log_signal_schema import LogSignal
from collections import deque
import glob
from utils.hash_utils import hash_gen

logger = logging.getLogger(__name__)


class LogAnalyzer:
    """Extracts structured error signals from CI pipeline log files.

    Scans stage-wise ``.log`` files for patterns matching known signal types
    (exit codes, errors, resource exhaustion, test failures, build failures),
    strips noise lines, captures surrounding context, and de-duplicates results
    by SHA-256 fingerprint.
    """

    def __init__(self):
        self.log_path = Path(settings.LOG_PATH)
        self.patterns = {
            SignalType.EXIT_CODE: [
                re.compile(r"\bexit\s*(?:code)?\s*(?!0\b)\d+\b", re.IGNORECASE),
                re.compile(r"\b(?:returned|status)\s*[=:]?\s*[1-9]\d*\b", re.IGNORECASE),
            ],
            SignalType.ERROR: [
                # (?!\.\w) prevents matching filenames like error.c / exception.py
                re.compile(
                    r"(?:^|\s|\[)(error|exception|traceback|panic|fatal|critical)\b(?!\.\w)",
                    re.IGNORECASE,
                ),
                # same guard for abort.c / crash.h etc.
                re.compile(
                    r"\b(abort(?:ed|ing)?|crash(?:ed|ing)?|segfault)\b(?!\.\w)",
                    re.IGNORECASE,
                ),
                re.compile(r"\bhttp\s*error\b", re.IGNORECASE),
                re.compile(r"\bhttperror\b", re.IGNORECASE),
                # network / connectivity
                re.compile(r"\bconnection\s+(?:refused|timed?\s*out|reset)\b", re.IGNORECASE),
                re.compile(r"\bcould\s+not\s+(?:connect|resolve\s+host)\b", re.IGNORECASE),
                re.compile(r"\bno\s+route\s+to\s+host\b", re.IGNORECASE),
                re.compile(r"\bname\s+or\s+service\s+not\s+known\b", re.IGNORECASE),
                # timeout
                re.compile(r"\b(?:execution\s+)?timed?\s*out\b(?!\.\w)", re.IGNORECASE),
                re.compile(r"\bdeadline\s+exceeded\b", re.IGNORECASE),
            ],
            SignalType.RESOURCE: [
                re.compile(r"\boom(?:killed)?\b(?!\.\w)", re.IGNORECASE),
                re.compile(r"\bout\s+of\s+memory\b", re.IGNORECASE),
                # disk exhaustion
                re.compile(r"\bno\s+space\s+left\s+on\s+device\b", re.IGNORECASE),
                re.compile(r"\bENOSPC\b"),
                re.compile(r"\bdisk\s+(?:quota\s+exceeded|full)\b", re.IGNORECASE),
            ],
            SignalType.TEST_FAILURE: [
                re.compile(r"\bassert(?:ion)?\s*failed\b", re.IGNORECASE),
                re.compile(r"\btests?\s+failed\b", re.IGNORECASE),
            ],
            SignalType.BUILD_FAILURE: [
                re.compile(r"\bbuild\s+failed\b", re.IGNORECASE),
                re.compile(r"\baccess\s+denied\b", re.IGNORECASE),
                # dependency resolution
                re.compile(r"\bcould\s+not\s+resolve\s+(?:dependencies?|artifact)\b", re.IGNORECASE),
                re.compile(r"\bcannot?\s+find\s+(?:module|package|symbol)\b", re.IGNORECASE),
                re.compile(r"\bno\s+matching\s+distribution\s+found\b", re.IGNORECASE),
                re.compile(r"\bnpm\s+err[!]?\b(?!\.\w)", re.IGNORECASE),
                re.compile(r"\bcompilation\s+(?:error|failed)\b", re.IGNORECASE),
                # container / registry
                re.compile(r"\bfailed\s+to\s+pull\s+(?:image\b)?", re.IGNORECASE),
                re.compile(r"\bpull\s+access\s+denied\b", re.IGNORECASE),
            ],
            SignalType.SECURITY: [
                re.compile(r"\b(?:authentication|auth)\s+failed\b", re.IGNORECASE),
                re.compile(r"\bunauthorized\b(?!\.\w)", re.IGNORECASE),
                re.compile(r"\bpermission\s+denied\b(?!\.\w)", re.IGNORECASE),
                re.compile(
                    r"\bssl\s+(?:certificate\s+)?(?:verify\s+failed|handshake\s+(?:failed|error))\b",
                    re.IGNORECASE,
                ),
            ],
        }
        
        self.noise_patterns = [
            # InfluxDB plugin decorator lines
            re.compile(
                r'\[InfluxDB\s+(P|p)lugin\]'
                r'(\s+Collecting|\s+INFO:|\s+WARNING:|.*plugin\s+data\s+found)',
                re.IGNORECASE,
            ),
            # Maven / Gradle dependency download progress lines
            re.compile(r'^\s*(?:Downloading|Downloaded)\s+from\s+\S+:', re.IGNORECASE),
            # Console progress bar lines e.g. "Progress (5/120)"
            re.compile(r'^\s*Progress\s*\(\s*\d+\s*/\s*\d+\s*\)', re.IGNORECASE),
            # Jenkins [Pipeline] structural decorator lines (no content after the keyword)
            re.compile(r'^\s*\[Pipeline\]\s+(?:\{|\}|//\s*\w+)\s*$'),
        ]

        # Handles ISO-8601, log4j, time-only, and bracketed timestamp prefixes
        self._ts_pattern = re.compile(
            r'\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\]?'
            r'|\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b',
            re.IGNORECASE,
        )

        # Priority order: most specific first, EXIT_CODE last (generic catch-all)
        self._pattern_priority = [
            SignalType.TEST_FAILURE,
            SignalType.BUILD_FAILURE,
            SignalType.SECURITY,
            SignalType.RESOURCE,
            SignalType.ERROR,
            SignalType.EXIT_CODE,
        ]

        # Multi-line error block triggers and their continuation patterns
        self._multiline_triggers = [
            # Python
            ('python_traceback', re.compile(r'traceback\s*\(most\s+recent\s+call\s+last\)\s*:', re.IGNORECASE)),
            # Java / Kotlin / Groovy JVM stack traces
            ('java_exception',   re.compile(r'exception\s+in\s+thread\b|[a-z][\w.]+\.(?:exception|error):\s', re.IGNORECASE)),
            # Rust thread panic
            ('rust_panic',       re.compile(r"thread\s+'.+?'\s+panicked\s+at", re.IGNORECASE)),
            # Go runtime panic
            ('go_panic',         re.compile(r'^panic:\s', re.IGNORECASE)),
            # Node.js / JavaScript / TypeScript — standard built-in error types
            ('node_error',       re.compile(r'^(?:Error|TypeError|ReferenceError|SyntaxError|RangeError|URIError|EvalError):\s', re.IGNORECASE)),
            # Ruby — plain or namespaced XxxError (listed after node_error so JS types match first)
            ('ruby_exception',   re.compile(r'^[A-Z][A-Za-z:]+Error:\s', re.IGNORECASE)),
            # PHP fatal / parse error
            ('php_fatal',        re.compile(r'^(?:Fatal|Parse)\s+error:\s', re.IGNORECASE)),
            # Dart / Flutter unhandled exception block
            ('dart_exception',   re.compile(r'^unhandled\s+exception:\s*$', re.IGNORECASE)),
            # Gradle build failure summary block
            ('gradle_failure',   re.compile(r'^FAILURE:\s+Build\s+failed\s+with\s+an\s+exception', re.IGNORECASE)),
        ]
        self._multiline_continuations = {
            # Indented stack frames + final "ExcType: msg" line
            'python_traceback': re.compile(r'^\s+File\s+"|^\s+|^[A-Za-z][\w.]*(?:Error|Exception)\s*:', re.IGNORECASE),
            # "  at pkg.Class.method(File.java:N)" or "Caused by:"
            'java_exception':   re.compile(r'^\s*at\s+[\w.$<>]+\(|^caused\s+by\s*:', re.IGNORECASE),
            # Rust compiler note/span markers
            'rust_panic':       re.compile(r'^note\s*:|^\s+\d+\s*\|\s|^\s+-+\^', re.IGNORECASE),
            # "goroutine N [running]:" header + tab-indented frame lines
            'go_panic':         re.compile(r'^goroutine\s+\d+\s+\[|^\t\S|^\s*\S.*\.go:\d+', re.IGNORECASE),
            # "  at functionName (file.js:N:N)" — parentheses distinguish from Java
            'node_error':       re.compile(r'^\s+at\s+(?:[\w.<>]+\s+)?\(|^\s+at\s+\S+:\d+:\d+', re.IGNORECASE),
            # "  from /path/file.rb:N:in 'method'"
            'ruby_exception':   re.compile(r'^\s+from\s+.+:\d+:in\s+', re.IGNORECASE),
            # "Stack trace:" header + "#N  ..." frame lines
            'php_fatal':        re.compile(r'^Stack\s+trace:|^\s*#\d+\s+', re.IGNORECASE),
            # "#N  ClassName.method (package:app/file.dart:N)"
            'dart_exception':   re.compile(r'^#\d+\s+[\w.<>\s]+\(', re.IGNORECASE),
            # "* What went wrong:", "> ...", "Execution failed for task:"
            'gradle_failure':   re.compile(r'^\*\s+(?:What\s+went\s+wrong|Try\s+)|^>\s+\S|^Execution\s+failed\s+for', re.IGNORECASE),
        }

        # JSON log line candidate field names checked in preference order
        self._json_msg_keys = ('msg', 'message', 'error', 'err', 'text', 'log')

        self.pre_content = 10
        self.post_context = 20
    
    def _is_noise_line(self, line: str) -> bool:
        """Return True if the line matches a known noise pattern and should be skipped.

        Args:
            line: A single log line to evaluate.

        Returns:
            ``True`` when the line matches at least one noise pattern.
        """
        return any(pattern.search(line) for pattern in self.noise_patterns)
    
    def _remove_timestamp(self, line: str) -> str:
        """Lowercase, strip, and remove ISO-8601 timestamp brackets from a line.

        Args:
            line: Raw log line that may contain a leading ``[YYYY-MM-DDTHH:MM:SS.mmmZ]``
                timestamp token.

        Returns:
            The cleaned line with the timestamp bracket removed.
        """
        line = line.lower().strip()
        return self._ts_pattern.sub('', line).strip()

    def _extract_json_text(self, line: str) -> str:
        """Extract the message field from a JSON-structured log line.

        Docker, Kubernetes, and many modern CI tools emit structured JSON logs
        such as ``{"level":"error","msg":"connection refused"}``.  This method
        parses the line and returns the value of the first recognised message key
        so that patterns are applied to human-readable text, not raw JSON.

        Args:
            line: Raw log line, possibly a JSON object string.

        Returns:
            Value of the first matched key (``msg``, ``message``, ``error``,
            ``err``, ``text``, ``log``) when the line is a JSON dict, otherwise
            the original line unchanged.
        """
        stripped = line.strip()
        if not stripped.startswith('{'):
            return line
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                for key in self._json_msg_keys:
                    if obj.get(key):
                        return str(obj[key])
        except (json.JSONDecodeError, ValueError):
            pass
        return line

    def _get_multiline_trigger(self, line: str) -> Optional[str]:
        """Return the block type name if *line* opens a known multi-line error block.

        Recognised block types:

        * ``'python_traceback'`` — ``Traceback (most recent call last):``
        * ``'java_exception'``   — ``Exception in thread`` / ``pkg.ExcClass: msg``
        * ``'rust_panic'``       — ``thread 'x' panicked at``
        * ``'go_panic'``         — ``panic: <msg>``
        * ``'node_error'``       — ``TypeError: ...`` and other JS built-in error types
        * ``'ruby_exception'``   — ``RuntimeError: ...`` and other ``XxxError:`` patterns
        * ``'php_fatal'``        — ``Fatal error:`` / ``Parse error:``
        * ``'dart_exception'``   — ``Unhandled exception:``
        * ``'gradle_failure'``   — ``FAILURE: Build failed with an exception``

        Args:
            line: Normalised (timestamp-removed) log line.

        Returns:
            Block type string when a trigger matched, otherwise ``None``.
        """
        for block_type, pattern in self._multiline_triggers:
            if pattern.search(line):
                return block_type
        return None

    def _is_multiline_continuation(self, block_type: str, line: str) -> bool:
        """Return True if *line* belongs to an ongoing multi-line error block.

        Args:
            block_type: Active block type key (e.g. ``'python_traceback'``).
            line: Normalised log line to test.

        Returns:
            ``True`` when *line* matches the continuation pattern for
            *block_type*.
        """
        pattern = self._multiline_continuations.get(block_type)
        return bool(pattern and pattern.search(line))

    def _resolve_multiline_block(self, block: dict) -> Tuple[str, str]:
        """Extract the key error message and pre-content from a completed block.

        Resolution strategy per block type:

        * **python_traceback** — last line (``ExcType: message``) is the
          ``error_line``; preceding stack frames become ``pre_content``.
        * **rust_panic** — message after ``panicked at`` is the ``error_line``;
          compiler notes become ``pre_content``.
        * **dart_exception** — trigger line is ``Unhandled exception:``; the
          following line is the real ``error_line``; stack frames are
          ``pre_content``.
        * **gradle_failure** — line after ``* What went wrong:`` is the
          ``error_line``; surrounding context is ``pre_content``.
        * **All others** (``java_exception``, ``go_panic``, ``node_error``,
          ``ruby_exception``, ``php_fatal``) — first line is the
          ``error_line``; stack frames / context become ``pre_content``.

        Args:
            block: Dict with keys ``'type'`` and ``'lines'``.

        Returns:
            Tuple of ``(error_line, pre_content_block)`` as plain strings.
        """
        block_type = block['type']
        lines      = block['lines']
        if not lines:
            return ('', '')
        if block_type == 'python_traceback':
            # Last line is the "ExcType: message" — everything before is the stack
            return lines[-1], '\n'.join(lines[:-1])
        if block_type == 'rust_panic':
            msg_match  = re.search(r"panicked at (.+)", lines[0], re.IGNORECASE)
            error_line = msg_match.group(1).strip("'\"") if msg_match else lines[0]
            return error_line, '\n'.join(lines[1:])
        if block_type == 'dart_exception':
            # Trigger line is the literal "Unhandled exception:" — real message is line 1
            if len(lines) > 1:
                extra_pre = '\n'.join(lines[2:]) if len(lines) > 2 else ''
                return lines[1], lines[0] + ('\n' + extra_pre if extra_pre else '')
            return lines[0], ''
        if block_type == 'gradle_failure':
            # Look for the line following "* What went wrong:" as the root cause
            for i, ln in enumerate(lines):
                if re.search(r'\*\s+what went wrong', ln, re.IGNORECASE) and i + 1 < len(lines):
                    root = lines[i + 1].lstrip('> ').strip()
                    ctx  = '\n'.join(lines[:i] + lines[i + 2:])
                    return root, ctx
            return lines[0], '\n'.join(lines[1:])
        # Default: java_exception, go_panic, node_error, ruby_exception, php_fatal
        # First line is the error message; remaining lines are stack frames → pre_content
        return lines[0], '\n'.join(lines[1:])

    def extract_signals(self, failure_id: str) -> List[LogSignal]:
        """Scan all stage log files for a failure and return de-duplicated signals.

        For every ``*.log`` file found under ``<LOG_PATH>/<failure_id>/``, the
        method reads each file line-by-line applying the following pipeline:

        1. JSON log lines are unwrapped to their message field.
        2. Multi-line error blocks (Python tracebacks, Java exceptions, Rust
           panics) are assembled by a per-language state machine.
        3. Remaining single lines are matched against priority-ordered regex
           patterns (specific types first, ``EXIT_CODE`` last).

        Pre- and post-context lines are captured around every signal and
        duplicates are removed by SHA-256 fingerprint.

        Args:
            failure_id: Unique identifier of the pipeline failure whose logs
                should be scanned.

        Returns:
            De-duplicated list of
            :class:`~api.schemas.log_signal_schema.LogSignal` objects.
        """
        failure_build_path = glob.glob(str(self.log_path / failure_id / '*.log'))
        if not failure_build_path:
            logger.warning("No log files found for failure_id '%s' under '%s'", failure_id, self.log_path)
        data = []

        for stagewise_log in failure_build_path:
            logger.debug("Scanning log file: %s", stagewise_log)
            result = []
            current_event   = None
            multiline_block = None
            iterate = 0
            
            with open(stagewise_log, 'r', encoding='utf-8') as log:
                precontent_queue = deque(maxlen=self.pre_content)
                postcontent_queue = deque(maxlen=self.post_context)
                
                for line in log:
                    line = re.sub(r'[^\x00-\x7F]+', '', line)
                    
                    if current_event is None and multiline_block is None:
                        if self._is_noise_line(line):
                            precontent_queue.append(line)
                            continue

                        line = self._remove_timestamp(self._extract_json_text(line))

                        # 1. Multi-line block trigger check
                        block_type = self._get_multiline_trigger(line)
                        if block_type:
                            multiline_block = {
                                'type':  block_type,
                                'lines': [line],
                                'stage': Path(stagewise_log).name.replace('.log', ''),
                            }
                            continue

                        # 2. Single-line patterns — specific types first, EXIT_CODE last
                        matched = False
                        for signal_type in self._pattern_priority:
                            for pattern in self.patterns[signal_type]:
                                if pattern.search(line):
                                    iterate = 0
                                    current_event = {
                                        'stage':       Path(stagewise_log).name.replace('.log', ''),
                                        'signal_type': signal_type,
                                        'error_line':  line,
                                        'pre_content': ''.join(precontent_queue),
                                    }
                                    current_event['fingerprint'] = hash_gen.fingerprint(
                                        error_line=current_event['error_line']
                                    )
                                    logger.debug(
                                        "Signal detected [%s] in stage '%s': %s",
                                        signal_type, current_event['stage'], line[:120],
                                    )
                                    matched = True
                                    break
                            if matched:
                                break

                        if matched:
                            continue  # don't add the error line to precontent_queue

                    elif multiline_block is not None:
                        cleaned = self._remove_timestamp(line)
                        if self._is_multiline_continuation(multiline_block['type'], cleaned):
                            multiline_block['lines'].append(cleaned)
                            continue
                        # Block ended — emit signal and begin post-context collection
                        error_line, block_pre = self._resolve_multiline_block(multiline_block)
                        current_event = {
                            'stage':       multiline_block['stage'],
                            'signal_type': SignalType.ERROR,
                            'error_line':  error_line,
                            'pre_content': ''.join(precontent_queue) + ('\n' + block_pre if block_pre else ''),
                        }
                        current_event['fingerprint'] = hash_gen.fingerprint(error_line)
                        logger.debug(
                            "Multi-line block [%s] resolved in stage '%s': %s",
                            multiline_block['type'], current_event['stage'], error_line[:120],
                        )
                        multiline_block = None
                        iterate = 1
                        postcontent_queue.append(line)
                        continue

                    elif current_event and iterate < self.post_context:
                        postcontent_queue.append(line)
                        iterate += 1
                        continue
                    
                    elif current_event and iterate == self.post_context:
                        current_event['post_content'] = ''.join(postcontent_queue)
                        result.append(
                            LogSignal(
                                stage=current_event["stage"],
                                signal_type=current_event["signal_type"],
                                fingerprint=current_event["fingerprint"],
                                error_line=current_event.get("error_line"),
                                pre_content=current_event.get("pre_content"),
                                post_content=current_event.get("post_content"),
                            )
                        )
                        current_event = None
                        postcontent_queue.clear()
                    
                    precontent_queue.append(line)
            
            # Flush any block still open at end-of-file
            if multiline_block is not None:
                error_line, block_pre = self._resolve_multiline_block(multiline_block)
                current_event = {
                    'stage':       multiline_block['stage'],
                    'signal_type': SignalType.ERROR,
                    'error_line':  error_line,
                    'pre_content': ''.join(precontent_queue) + ('\n' + block_pre if block_pre else ''),
                }
                current_event['fingerprint'] = hash_gen.fingerprint(error_line)

            if current_event is not None:
                current_event["post_content"] = "".join(postcontent_queue)
                result.append(
                    LogSignal(
                        stage=current_event["stage"],
                        signal_type=current_event["signal_type"],
                        fingerprint=current_event["fingerprint"],
                        error_line=current_event.get("error_line"),
                        pre_content=current_event.get("pre_content"),
                        post_content=current_event.get("post_content"),
                    )
                )
            
            data.extend(result)
        
        result = self.filter_duplicate_issues(data)
        return result


    def filter_duplicate_issues(self, errors: List[LogSignal]) -> List[LogSignal]:
        """Remove signals that share the same fingerprint, keeping the first occurrence.

        Args:
            errors: List of :class:`~api.schemas.log_signal_schema.LogSignal` objects,
                potentially containing duplicates.

        Returns:
            List with duplicate fingerprints removed, preserving insertion order.
        """
        duplicate_hash = set()
        result = []
        for err_value in errors:
            if err_value.fingerprint not in duplicate_hash:
                result.append(err_value)
                duplicate_hash.add(err_value.fingerprint)
        return result


log_analyzer_obj = LogAnalyzer()