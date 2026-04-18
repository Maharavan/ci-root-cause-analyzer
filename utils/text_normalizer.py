import re


class TextNormalizer:
    """Collection of text normalisation helpers for pipeline log analysis.

    Methods:
        normalize_error_line: Lightly normalises a single error line to produce
            stable SHA-256 fingerprints.  Preserves enough variation to
            distinguish unique errors while stripping volatile tokens
            (timestamps, absolute paths, numeric values, hex IDs).
        normalize_for_embedding: Aggressively normalises log text before
            generating embeddings for semantic similarity matching.  Removes or
            replaces timestamps, file paths, memory addresses, hashes, and large
            numbers with placeholder tokens so that structurally similar errors
            cluster together regardless of incidental variation.
    """

    @staticmethod
    def normalize_error_line(line: str):
        """
        Normalize error line for fingerprinting (unique identification).
        This should be LESS aggressive to preserve uniqueness.
        """
        if not line:
            return ""
        
        line = line.lower()
        line = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', line)
        line = re.sub(r'\[\d{4}-\d{2}-\d{2}t\d{2}:\d{2}:\d{2}\.\d{3}z\]', '', line)
        line = re.sub(r'[a-zA-Z]:\\[^\s]+', '<PATH>', line)
        line = re.sub(r'/[^\s]+', '<PATH>', line)
        line = re.sub(r'\d+', '<NUM>', line)
        line = re.sub(r'#[a-f0-9]+', '#<ID>', line)
        line = re.sub(r'[^\w\s<>]', ' ', line)
        line = re.sub(r'\s+', ' ', line).strip()
        return line
    
    @staticmethod
    def normalize_for_embedding(text: str) -> str:
        """
        Normalize text for embedding generation (similarity matching).
        """
        if not text:
            return ""
        
        text = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', text)
        text = re.sub(r'\d{2}:\d{2}:\d{2}\.\d+', '<TIME>', text)  
        text = re.sub(r'\d{2}:\d{2}:\d{2}', '<TIME>', text) 
        text = re.sub(r'\d{2}:\d{2}', '<TIME>', text)
        text = re.sub(r'\[\d{4}-\d{2}-\d{2}t\d{2}:\d{2}:\d{2}\.\d{3}z\]', '<TIMESTAMP>', text)
        text = re.sub(r'\d{2}:\d{2}\s+\+\d+\s+~\d+\s+-?\d+:', '<TEST_PROGRESS>', text)
        text = re.sub(r'[a-z]:[/\\][\w/\\]+[/\\](\w+\.dart)', r'\1', text, flags=re.IGNORECASE)
        text = re.sub(r'[a-z]:[/\\][\w/\\]+[/\\](\w+\.\w+)', r'\1', text, flags=re.IGNORECASE)
        text = re.sub(r'/[\w/]+/(\w+\.dart)', r'\1', text)
        text = re.sub(r'/[\w/]+/(\w+\.\w+)', r'\1', text)
        text = re.sub(r'[a-zA-Z]:\\[^\s]+', '<PATH>', text)
        text = re.sub(r'/[^\s/]+/[^\s]+', '<PATH>', text)
        
        text = re.sub(r'line\s+\d+', 'line <NUM>', text, flags=re.IGNORECASE)
        text = re.sub(r'column\s+\d+', 'column <NUM>', text, flags=re.IGNORECASE)
        text = re.sub(r':\d+:\d+:', ':<NUM>:<NUM>:', text)  # :112:67:
        text = re.sub(r'at line \d+', 'at line <NUM>', text, flags=re.IGNORECASE)
        text = re.sub(r'test\s+\d+', 'test <NUM>', text, flags=re.IGNORECASE)
        text = re.sub(r'\+\d+', '+<NUM>', text)
        text = re.sub(r'~\d+', '~<NUM>', text)
        
        text = re.sub(r'0x[0-9a-f]+', '<ADDR>', text, flags=re.IGNORECASE)
        text = re.sub(r'\b[0-9a-f]{8,}\b', '<HASH>', text, flags=re.IGNORECASE)
        text = re.sub(r'#[a-f0-9]+', '#<ID>', text)
        text = re.sub(r'\b\d+\.\d+\b', '<FLOAT>', text)
        text = re.sub(r'\b\d{4,}\b', '<BIGNUM>', text)
        text = re.sub(r'^(error|exception|warning|fatal):', '<ERROR_TYPE>:', text, flags=re.IGNORECASE)
        text = re.sub(r'test\s+(failed|failure|error)', 'test <FAILED>', text, flags=re.IGNORECASE)
        text = re.sub(r'(when the exception was thrown|exception caught|stack trace)', '<EXCEPTION_MARKER>', text, flags=re.IGNORECASE)
        text = text.lower()
        
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    
text_norm_obj = TextNormalizer()