"""Keep native tool authority with host-owned function/custom implementations."""


def _named_tool(value, kinds):
    if (type(value) is not dict or type(value.get("type")) is not str or value["type"] not in kinds
            or type(value.get("name")) is not str or not 1 <= len(value["name"]) <= 256
            or value.get("namespace") is not None and type(value["namespace"]) is not str):
        raise ValueError("native_tool_unsupported")
    return value["type"]


def _declarations(tools, *, namespaces=True):
    if type(tools) is not list or len(tools) > 256:
        raise ValueError("native_tools_invalid")
    kinds = {"function", "custom", "namespace"} if namespaces else {"function", "custom"}
    for tool in tools:
        if _named_tool(tool, kinds) == "namespace":
            _declarations(tool.get("tools"), namespaces=False)


def validate_native_tools(payload):
    # Parameters, output schemas and custom input grammars describe client
    # code/data. Only namespace.tools contains nested active declarations.
    _declarations(payload.get("tools", []))
    choice = payload.get("tool_choice")
    if choice is None:
        return
    if type(choice) is str:
        if choice not in {"auto", "none", "required"}:
            raise ValueError("native_tool_choice_unsupported")
        return
    if type(choice) is not dict or type(choice.get("type")) is not str:
        raise ValueError("native_tool_choice_invalid")
    if choice["type"] == "allowed_tools":
        if (type(choice.get("mode")) is not str or choice["mode"] not in {"auto", "required"}
                or type(choice.get("tools")) is not list or len(choice["tools"]) > 256):
            raise ValueError("native_tool_choice_invalid")
        for tool in choice["tools"]:
            _named_tool(tool, {"function", "custom"})
    else:
        _named_tool(choice, {"function", "custom"})
