"""Reversible Responses transport; the coding client owns tool execution."""
import copy
import hashlib
import json
import re

CAPABILITY = 'codex_llama_v1'
MAX_FRAME = 2 * 1024 * 1024
PARENT_TAGS = {'parent_turn_id', 'x-codex-parent-thread-id', 'x-openai-subagent'}


class CodexLlamaError(ValueError):
    pass


def needs_adapter(value):
    tools = value.get('tools', [])
    history = value.get('input', [])
    return (value.get('reasoning', {}) not in ({}, {'effort': 'none'})
            or isinstance(value.get('client_metadata'), dict) and bool(PARENT_TAGS.intersection(value['client_metadata']))
            or isinstance(tools, list) and any(isinstance(tool, dict) and tool.get('type') in ('namespace', 'custom', 'tool_search', 'web_search') for tool in tools)
            or isinstance(history, list) and any(isinstance(item, dict) and ('namespace' in item
                or item.get('type') in ('custom_tool_call', 'custom_tool_call_output', 'tool_search_call', 'tool_search_output', 'reasoning')) for item in history))


class CodexLlamaRequest:
    def __init__(self, value):
        self.value = copy.deepcopy(value)
        self.identities = {}
        self.declarations = {}
        self.buffer = bytearray()
        self.stream_kinds = {}
        tags = value.get('client_metadata', {})
        if isinstance(tags, dict) and any(key in tags and (not isinstance(tags[key], str)
                or not 0 < len(tags[key]) <= 2048 or any(ord(c) < 32 or ord(c) > 126 for c in tags[key]))
                for key in PARENT_TAGS):
            raise CodexLlamaError('invalid_parent_metadata')
        reasoning = value.get('reasoning', {})
        if ('chat_template_kwargs' in value or type(reasoning) is not dict
                or set(reasoning) - {'effort', 'summary'}
                or reasoning.get('effort', 'medium') not in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
                or reasoning.get('summary', 'auto') not in {'auto', 'concise', 'detailed'}):
            raise CodexLlamaError('unsupported_reasoning')
        self.value['chat_template_kwargs'] = {'enable_thinking': reasoning.get('effort') != 'none'}
        tools = value.get('tools', [])
        if type(tools) is not list or len(tools) > 1024:
            raise CodexLlamaError('invalid_tools')
        self.client_discovery = any(isinstance(tool, dict) and tool.get('type') == 'tool_search'
                                    and tool.get('execution') == 'client' for tool in tools)
        flattened = []
        loaded = [tool for item in value.get('input', []) if isinstance(item, dict)
                  and item.get('type') == 'tool_search_output' for tool in item.get('tools', [])]
        for tool, replayed in [*((tool, False) for tool in tools), *((tool, True) for tool in loaded)]:
            if type(tool) is not dict:
                raise CodexLlamaError('invalid_tool')
            if tool.get('type') == 'namespace':
                if (set(tool) - {'type', 'name', 'description', 'tools'}
                        or not isinstance(tool.get('name'), str) or not tool['name']
                        or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', tool['name'])
                        or not isinstance(tool.get('description', ''), str)
                        or type(tool.get('tools')) is not list or not tool['tools']):
                    raise CodexLlamaError('invalid_namespace')
                for child in tool['tools']:
                    converted = self._tool(child, tool['name'], replayed=replayed)
                    if converted is not None:
                        flattened.append(converted)
            else:
                converted = self._tool(tool, None, replayed=replayed)
                if converted is not None:
                    flattened.append(converted)
            if len(flattened) > 1024:
                raise CodexLlamaError('too_many_tools')
        if 'tools' in self.value:
            self.value['tools'] = flattened
        if isinstance(self.value.get('input'), list):
            self.value['input'] = [self._input(item) for item in self.value['input']]

    def _input(self, item):
        if isinstance(item, dict) and item.get('status') not in {None, 'completed'}:
            raise CodexLlamaError('incomplete_tool_history')
        if isinstance(item, dict) and item.get('type') == 'reasoning':
            if item.get('encrypted_content') or set(item) - {'type', 'id', 'summary', 'content', 'encrypted_content', 'status'}:
                raise CodexLlamaError('unsupported_reasoning_history')
            content = item.get('content') or item.get('summary')
            if (type(content) is not list or not content or any(type(part) is not dict
                    or set(part) != {'type', 'text'} or part.get('type') not in {'reasoning_text', 'summary_text'}
                    or not isinstance(part.get('text'), str) for part in content)):
                raise CodexLlamaError('invalid_reasoning_history')
            item = {key: value for key, value in item.items() if key != 'encrypted_content'} | {
                'content': [{'type': 'reasoning_text', 'text': '\n'.join(part['text'] for part in content)}]}
        elif isinstance(item, dict) and item.get('type') == 'tool_search_call':
            if item.get('execution') != 'client' or not isinstance(item.get('arguments'), dict):
                raise CodexLlamaError('unsupported_tool_discovery')
            item = {key: value for key, value in item.items() if key not in {'execution', 'arguments', 'status'}} | {
                'type': 'function_call', 'name': 'tool_search', 'arguments': json.dumps(item['arguments'], separators=(',', ':'))}
        elif isinstance(item, dict) and item.get('type') == 'tool_search_output':
            if item.get('execution') != 'client' or not isinstance(item.get('tools'), list):
                raise CodexLlamaError('unsupported_tool_discovery')
            discovered = set()
            for tool in item['tools']:
                if tool.get('type') == 'namespace':
                    discovered.update((tool['name'], child['name']) for child in tool['tools'])
                else:
                    discovered.add((None, tool['name']))
            aliases = {alias for alias, identity in self.identities.items() if identity[:2] in discovered}
            item = {key: value for key, value in item.items() if key not in {'execution', 'tools', 'status'}} | {
                'type': 'function_call_output', 'output': json.dumps({'tools': [
                    tool for tool in self.value['tools'] if tool['name'] in aliases]}, separators=(',', ':'))}
        elif isinstance(item, dict) and item.get('type') in {'function_call', 'custom_tool_call'}:
            aliases = [alias for alias, identity in self.identities.items()
                       if identity[:2] == (item.get('namespace'), item.get('name'))]
            if len(aliases) != 1:
                raise CodexLlamaError('undeclared_tool_history')
            item = {key: value for key, value in item.items() if key != 'namespace'}
            item['name'] = aliases[0]
            if item['type'] == 'custom_tool_call':
                if not isinstance(item.get('input'), str):
                    raise CodexLlamaError('invalid_custom_history')
                item = {key: value for key, value in item.items() if key != 'input'} | {
                    'type': 'function_call', 'arguments': json.dumps({'input': item['input']}, separators=(',', ':'))}
        elif isinstance(item, dict) and item.get('type') == 'custom_tool_call_output':
            item = {**item, 'type': 'function_call_output'}
        return item

    def _tool(self, tool, namespace, *, replayed=False):
        if type(tool) is not dict or tool.get('type') not in {'function', 'custom', 'tool_search', 'web_search'}:
            raise CodexLlamaError('unsupported_tool')
        name = ('tool_search' if tool['type'] == 'tool_search' else
                'hormuz_web_search' if tool['type'] == 'web_search' else tool.get('name'))
        if not isinstance(name, str) or not name or len(name) > 256:
            raise CodexLlamaError('invalid_tool_name')
        alias = name if namespace is None else 'hormuz_' + namespace + '__' + name
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', alias):
            if namespace is None:
                raise CodexLlamaError('invalid_tool_name')
            alias = 'hormuz_h_' + hashlib.sha256((namespace + '\0' + name).encode()).hexdigest()[:32]
        if alias in self.identities:
            if replayed and self.identities[alias] == (namespace, name, tool['type']) and self.declarations[alias] == tool:
                return None
            raise CodexLlamaError('ambiguous_tool_name')
        kind = tool['type']
        self.identities[alias] = (namespace, name, kind)
        self.declarations[alias] = tool
        result = copy.deepcopy(tool)
        result['name'] = alias
        if 'defer_loading' in result:
            if type(result.pop('defer_loading')) is not bool:
                raise CodexLlamaError('invalid_deferred_tool')
        if kind == 'tool_search':
            if (namespace is not None or tool.get('execution') != 'client'
                    or set(tool) - {'type', 'execution', 'description', 'parameters'}
                    or not isinstance(tool.get('parameters'), dict)):
                raise CodexLlamaError('unsupported_tool_discovery')
            result = {key: item for key, item in result.items() if key != 'execution'} | {'type': 'function', 'strict': False}
        if kind == 'web_search':
            if (namespace is not None or not self.client_discovery or tool.get('external_web_access') is not True
                    or set(tool) != {'type', 'external_web_access'}):
                raise CodexLlamaError('unsupported_hosted_search')
            result = {'type': 'function', 'name': alias, 'strict': True,
                      'description': 'Discover the existing client web search MCP tool. After discovery, call that tool to perform the requested search. This discovery function does not fetch web results.',
                      'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}},
                                     'required': ['query'], 'additionalProperties': False}}
        if kind == 'custom':
            form = tool.get('format', {})
            if (set(tool) - {'type', 'name', 'description', 'format', 'defer_loading'} or type(form) is not dict
                    or form.get('type') != 'grammar' or form.get('syntax') not in {'lark', 'regex'}
                    or set(form) != {'type', 'syntax', 'definition'} or not isinstance(form['definition'], str)):
                raise CodexLlamaError('unsupported_custom_format')
            result = {'type': 'function', 'name': alias,
                      'description': tool.get('description', '') + '\nReturn the raw tool input in input. Grammar:\n' + form['definition'],
                      'strict': True, 'parameters': {'type': 'object', 'properties': {'input': {'type': 'string'}},
                                                    'required': ['input'], 'additionalProperties': False}}
        if namespace:
            result['description'] = namespace + '.' + name + ': ' + result.get('description', '')
        return result

    def admission_value(self):
        result = {key: value for key, value in self.value.items() if key != 'chat_template_kwargs'}
        if 'reasoning' in result:
            result['reasoning'] = {}
        if isinstance(result.get('client_metadata'), dict):
            result['client_metadata'] = {key: value for key, value in result['client_metadata'].items()
                                         if key not in PARENT_TAGS}
        if isinstance(result.get('input'), list):
            result['input'] = [{'type': 'message', 'role': 'assistant', 'content': item['content'][0]['text']}
                               if isinstance(item, dict) and item.get('type') == 'reasoning' else
                               {key: value for key, value in item.items() if key != 'status'} if isinstance(item, dict) else item
                               for item in result['input']]
        return result

    def _item(self, value, *, complete=True):
        if not isinstance(value, dict):
            raise CodexLlamaError('invalid_provider_item')
        if value.get('type') != 'function_call':
            return value
        if not isinstance(value.get('name'), str):
            raise CodexLlamaError('invalid_provider_tool_name')
        identity = self.identities.get(value.get('name'))
        if identity is None:
            raise CodexLlamaError('undeclared_tool_call')
        namespace, name, kind = identity
        result = {**value, 'name': name}
        if namespace:
            result['namespace'] = namespace
        if kind == 'custom':
            if not complete:
                raw_input = ''
            else:
                arguments = json.loads(value.get('arguments', ''))
                if type(arguments) is not dict or set(arguments) != {'input'} or not isinstance(arguments['input'], str):
                    raise CodexLlamaError('invalid_custom_arguments')
                raw_input = arguments['input']
            result = {key: item for key, item in result.items() if key != 'arguments'} | {
                'type': 'custom_tool_call', 'input': raw_input}
        elif kind in {'tool_search', 'web_search'}:
            arguments = json.loads(value.get('arguments', '{}')) if complete else {}
            if type(arguments) is not dict:
                raise CodexLlamaError('invalid_tool_discovery_arguments')
            if kind == 'web_search':
                if complete and (set(arguments) != {'query'} or not isinstance(arguments['query'], str)):
                    raise CodexLlamaError('invalid_web_search_arguments')
                arguments = {'query': 'search_service web search', 'limit': 1}
            result = {key: item for key, item in result.items() if key not in {'name', 'namespace', 'arguments'}} | {
                'type': 'tool_search_call', 'execution': 'client', 'arguments': arguments}
        return result

    def _response(self, value):
        if not isinstance(value, dict):
            raise CodexLlamaError('invalid_provider_response')
        if isinstance(value.get('output'), list):
            value = {**value, 'output': [self._item(item) for item in value['output']]}
        return value

    def _event(self, value):
        if not isinstance(value, dict):
            raise CodexLlamaError('invalid_provider_event')
        if value.get('type') in {'response.output_item.added', 'response.output_item.done'}:
            item = self._item(value['item'], complete=value['type'].endswith('.done'))
            if item.get('id'):
                self.stream_kinds[item['id']] = item['type']
            value = {**value, 'item': item}
        if (value.get('type', '').startswith('response.function_call_arguments.')
                and self.stream_kinds.get(value.get('item_id')) in {'custom_tool_call', 'tool_search_call'}):
            return None
        if isinstance(value.get('response'), dict):
            value = {**value, 'response': self._response(value['response'])}
        return value

    def feed(self, raw, *, event_stream, final=False):
        try:
            return self._feed(raw, event_stream=event_stream, final=final)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise CodexLlamaError('invalid_provider_frame') from error

    def _feed(self, raw, *, event_stream, final=False):
        self.buffer.extend(raw)
        if len(self.buffer) > MAX_FRAME:
            raise CodexLlamaError('provider_frame_exceeded')
        if not event_stream:
            if not final:
                return b''
            value = self._response(json.loads(self.buffer))
            self.buffer.clear()
            return json.dumps(value, separators=(',', ':')).encode()
        output = bytearray()
        self.buffer[:] = self.buffer.replace(b'\r\n', b'\n')
        while b'\n\n' in self.buffer or final and self.buffer:
            if b'\n\n' in self.buffer:
                frame, _, rest = self.buffer.partition(b'\n\n')
                self.buffer[:] = rest
            else:
                frame = bytes(self.buffer)
                self.buffer.clear()
            lines = []
            skip = False
            for line in frame.split(b'\n'):
                if line.startswith(b'data:') and line[5:].strip() != b'[DONE]':
                    value = self._event(json.loads(line[5:]))
                    if value is None:
                        skip = True
                        break
                    line = b'data: ' + json.dumps(value, separators=(',', ':')).encode()
                lines.append(line)
            if not skip:
                output.extend(b'\n'.join(lines) + b'\n\n')
        return bytes(output)
