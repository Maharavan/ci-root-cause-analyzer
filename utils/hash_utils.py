import hashlib
from utils.text_normalizer import text_norm_obj


class HashGenerator:
    """Generates deterministic SHA-256 fingerprints for error lines."""

    @staticmethod
    def fingerprint(error_line: str) -> str:
        """Compute a stable SHA-256 fingerprint for a raw error line.

        The line is first normalised (lowercased, timestamps/paths/numbers
        replaced with placeholders) so that semantically identical errors from
        different builds produce the same fingerprint.

        Args:
            error_line: Raw error line extracted from a pipeline log.

        Returns:
            Hex-encoded SHA-256 digest string (64 characters).
        """
        normalized = text_norm_obj.normalize_error_line(error_line)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

hash_gen = HashGenerator()