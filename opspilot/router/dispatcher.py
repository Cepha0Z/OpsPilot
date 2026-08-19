from ..core.tool_spec import get_tool_spec


def dispatch(request):

    tool = request["tool"]

    spec = get_tool_spec(tool)
    parameters = request.get("parameters", {})
    spec.validate_input(parameters, allow_references=False)
    return spec.handler(parameters)
