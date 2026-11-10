from backend._compat import publish_legacy_package
from backend.ingest.decoder import (
    MEDIA_JSON,
    MEDIA_PROTOBUF,
    BadRequestError,
    DecodedBatch,
    IngestError,
    PayloadTooLargeError,
    RawResourceSpans,
    RawSpan,
    UnsupportedMediaTypeError,
    decode_otlp,
)
from backend.ingest.normalize import (
    NormalizedBatch,
    RedactionProfile,
    ResourceInfo,
    SourceProfile,
    normalize,
)

__all__ = [
    "MEDIA_JSON",
    "MEDIA_PROTOBUF",
    "BadRequestError",
    "DecodedBatch",
    "IngestError",
    "NormalizedBatch",
    "PayloadTooLargeError",
    "RawResourceSpans",
    "RawSpan",
    "RedactionProfile",
    "ResourceInfo",
    "SourceProfile",
    "UnsupportedMediaTypeError",
    "decode_otlp",
    "normalize",
]

publish_legacy_package(__name__, ["decoder", "normalize"])
