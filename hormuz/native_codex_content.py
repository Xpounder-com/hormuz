"""Qualified native text content; active media/provider references stay closed."""

ANNOTATION_FIELDS = {
    "url_citation": (("url", "title"), ("start_index", "end_index")),
    "file_citation": (("file_id", "filename"), ("index",)),
    "container_file_citation": (("container_id", "file_id", "filename"), ("start_index", "end_index")),
    "file_path": (("file_id",), ("index",)),
}


def validate_annotations(annotations):
    if type(annotations) is not list:
        raise ValueError("native_annotation_invalid")
    for annotation in annotations:
        if (type(annotation) is not dict or type(annotation.get("type")) is not str
                or annotation["type"] not in ANNOTATION_FIELDS):
            raise ValueError("native_annotation_invalid")
        strings, indices = ANNOTATION_FIELDS[annotation["type"]]
        if (any(type(annotation.get(field)) is not str for field in strings)
                or any(type(annotation.get(field)) is not int or annotation[field] < 0 for field in indices)
                or "end_index" in indices and annotation["end_index"] < annotation["start_index"]):
            raise ValueError("native_annotation_invalid")


def validate_text_content(value, *, assistant=False):
    if type(value) is str:
        return
    if type(value) is not list:
        raise ValueError("native_content_invalid")
    for part in value:
        if type(part) is not dict or type(part.get("type")) is not str:
            raise ValueError("native_content_invalid")
        kind = part["type"]
        field = "text"
        if kind == "input_text":
            fields = {"type", "text", "prompt_cache_breakpoint"}
            cache_breakpoint = part.get("prompt_cache_breakpoint")
            if cache_breakpoint is not None and cache_breakpoint != {"mode": "explicit"}:
                raise ValueError("native_content_invalid")
        elif assistant and kind == "output_text":
            fields = {"type", "text", "annotations", "logprobs"}
            validate_annotations(part.get("annotations"))
            if part.get("logprobs") is not None and type(part["logprobs"]) is not list:
                raise ValueError("native_content_invalid")
        elif assistant and kind == "refusal":
            fields, field = {"type", "refusal"}, "refusal"
        else:
            raise ValueError("native_content_unsupported")
        if set(part) - fields or type(part.get(field)) is not str:
            raise ValueError("native_content_invalid")
