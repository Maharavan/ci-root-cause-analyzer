import logging
import os
from pathlib import Path
import re
import json
from typing import Dict, List, Optional

import numpy as np
from api.app.config import settings
from api.schemas.classified_schema import ClassifiedSignal
from api.schemas.rca_schema import SignalRCA

logger = logging.getLogger(__name__)


class LogStorer:
    """Handles reading and writing of all artefacts under ``storage/logs/``."""

    def __init__(self) -> None:
        self.STORAGE_PATH = Path(settings.LOG_PATH)

    def sanitize_filename(self, name: str) -> str:
        """
        Sanitise a stage name so it is safe to use as a filename.

        Strips leading/trailing whitespace, lowercases the string, and
        replaces any character that is not alphanumeric, a dot, hyphen or
        underscore with ``_``.

        Args:
            name: Raw stage name string.

        Returns:
            Filesystem-safe filename string.
        """
        name = name.strip().lower()
        return re.sub(r"[^a-zA-Z0-9._-]", "_", name)

    def write_stage_log(
        self,
        failure_id: str,
        stage_name: str,
        log_content: str,
    ) -> Path:
        """
        Append raw CI stage log content to ``<failure_id>/<stage>.log``.

        The directory is created if it does not already exist.  Content is
        appended rather than overwritten so multiple calls accumulate log
        lines in the correct order.

        Args:
            failure_id:  UUID of the failure record.
            stage_name:  CI stage name (sanitised to a safe filename).
            log_content: Raw log text to persist.

        Returns:
            :class:`pathlib.Path` of the written log file.
        """
        failure_dir = self.STORAGE_PATH / failure_id
        failure_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self.sanitize_filename(stage_name)}.log"
        log_path = failure_dir / filename
        with log_path.open("a", encoding="utf-8", errors="ignore") as fh:
            fh.write(log_content)
        return log_path

    def _write_json_log(
        self,
        failure_id: str,
        signals: List,
        filename: str,
    ) -> Path:
        """
        Serialize a list of Pydantic models to a JSON log file.

        If the file already exists its current contents are merged with the
        new records so previous entries are never lost.

        Args:
            failure_id: UUID of the failure record.
            signals:    List of Pydantic models that have a ``model_dump()`` method.
            filename:   Output filename (e.g. ``error.json``).

        Returns:
            :class:`pathlib.Path` of the written JSON file.
        """
        failure_dir = self.STORAGE_PATH / failure_id
        failure_dir.mkdir(parents=True, exist_ok=True)
        log_path = failure_dir / filename

        existing: List = []
        if log_path.exists():
            with open(log_path, 'r', encoding='utf-8') as fh:
                existing = json.load(fh)

        merged = [sig.model_dump() for sig in signals] + existing

        with open(log_path, 'w', encoding='utf-8') as fh:
            json.dump(merged, fh, indent=4)
        return log_path

    def write_classified_log(
        self,
        failure_id: str,
        classified_signal: List[ClassifiedSignal],
    ) -> Path:
        """
        Persist classified signals to ``<failure_id>/error.json``.

        Args:
            failure_id:        UUID of the failure record.
            classified_signal: Classified signal list to serialise.

        Returns:
            Path to the written JSON file.
        """
        return self._write_json_log(
            failure_id=failure_id,
            signals=classified_signal,
            filename="error.json",
        )

    def write_root_cause_analysis(
        self,
        failure_id: str,
        root_cause_signal: List[SignalRCA],
    ) -> Path:
        """
        Persist RCA results to ``<failure_id>/root_cause.json``.

        Args:
            failure_id:        UUID of the failure record.
            root_cause_signal: RCA signal list to serialise.

        Returns:
            Path to the written JSON file.
        """
        return self._write_json_log(
            failure_id=failure_id,
            signals=root_cause_signal,
            filename="root_cause.json",
        )

    def write_embeddings(
        self,
        failure_id: str,
        embeddings_dict: Dict[str, List[float]],
    ) -> None:
        """
        Persist a fingerprint-to-embedding mapping as JSON.

        Args:
            failure_id:      UUID of the failure record.
            embeddings_dict: Mapping of signal fingerprint → float list.
        """
        embeddings_path = self.STORAGE_PATH / failure_id / "embeddings.json"
        with open(embeddings_path, 'w', encoding='utf-8') as fh:
            json.dump(embeddings_dict, fh)
        logger.debug("Stored %d embeddings for %s.", len(embeddings_dict), failure_id)

    def read_embeddings(self, failure_id: str) -> Dict[str, List[float]]:
        """
        Load the embedding map for a failure from disk.

        Args:
            failure_id: UUID of the failure record.

        Returns:
            Mapping of fingerprint → float list, or an empty dict if the file
            does not exist.
        """
        embeddings_path = self.STORAGE_PATH / failure_id / "embeddings.json"
        if not embeddings_path.exists():
            logger.warning("No embeddings file found for failure_id=%s.", failure_id)
            return {}
        with open(embeddings_path, 'r', encoding='utf-8') as fh:
            embeddings_dict = json.load(fh)
        logger.debug("Loaded %d embeddings for %s.", len(embeddings_dict), failure_id)
        return embeddings_dict

    def get_embedding_for_signal(
        self,
        failure_id: str,
        fingerprint: str,
    ) -> Optional[np.ndarray]:
        """
        Retrieve the embedding for a specific signal by fingerprint.

        Args:
            failure_id:  UUID of the failure record.
            fingerprint: SHA-256 fingerprint of the signal.

        Returns:
            1-D float32 numpy array if found, otherwise ``None``.
        """
        embeddings_dict = self.read_embeddings(failure_id)
        if fingerprint in embeddings_dict:
            return np.array(embeddings_dict[fingerprint], dtype=np.float32)
        return None


log_obj = LogStorer()
