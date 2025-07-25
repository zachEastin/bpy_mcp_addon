"""
Network listener for BPY MCP extension.

This module implements a WebSocket server that receives JSON-formatted commands
and executes them within the Blender Python environment.
"""

import asyncio
import json
import secrets
import socket
import threading
import traceback
from typing import Any

from . import __package__, task_queue

try:
    import bpy
    import bpy.utils
except ImportError:
    # Allow importing outside of Blender for testing
    bpy = None

def _get_addon_name() -> str:
    """Get the addon name safely."""
    if __package__:
        return __package__
    return "bpy_mcp_addon"
_server_task: asyncio.Task | None = None
_server_loop: asyncio.AbstractEventLoop | None = None
_server_thread: threading.Thread | None = None
_current_token: str | None = None
_connections: set = set()


class BPYMCPProtocol:
    """Simple TCP protocol handler for BPY MCP commands."""
    
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Initialize the protocol handler."""
        self.reader = reader
        self.writer = writer
        self.authenticated = False
    
    async def handle_connection(self):
        """Handle incoming connection and process messages."""
        client_addr = self.writer.get_extra_info('peername')
        print(f"BPY MCP: New connection from {client_addr}")
        
        try:
            _connections.add(self)
            
            while True:
                # Read message length
                length_data = await self.reader.readexactly(4)
                if not length_data:
                    break
                
                message_length = int.from_bytes(length_data, byteorder='big')
                
                # Read message data
                message_data = await self.reader.readexactly(message_length)
                message_str = message_data.decode('utf-8')
                
                # Process the message
                response = await self.process_message(message_str)
                
                # Send response only if it's not empty (streaming responses are sent separately)
                if response:
                    response_data = response.encode('utf-8')
                    response_length = len(response_data).to_bytes(4, byteorder='big')
                    
                    self.writer.write(response_length + response_data)
                    await self.writer.drain()
                
        except asyncio.IncompleteReadError:
            print(f"BPY MCP: Client {client_addr} disconnected")
        except Exception as e:
            print(f"BPY MCP: Error handling connection from {client_addr}: {e}")
            traceback.print_exc()
        finally:
            _connections.discard(self)
            self.writer.close()
            await self.writer.wait_closed()
    
    async def process_message(self, message_str: str) -> str:
        """Process a JSON message and return response."""
        try:
            message = json.loads(message_str)
            
            # Check for required fields
            if 'id' not in message:
                return json.dumps({
                    'error': 'Missing required field: id',
                    'id': None
                })
            
            message_id = message['id']
            
            # Authentication check
            if not self.authenticated:
                if 'token' not in message:
                    return json.dumps({
                        'id': message_id,
                        'error': 'Authentication required: missing token',
                        'authenticated': False
                    })
                
                if _current_token and message['token'] != _current_token:
                    return json.dumps({
                        'id': message_id,
                        'error': 'Authentication failed: invalid token',
                        'authenticated': False
                    })
                
                self.authenticated = True
                
                # If this was just an auth message, return success
                if 'code' not in message and 'handler' not in message:
                    return json.dumps({
                        'id': message_id,
                        'authenticated': True,
                        'blender_version': getattr(bpy.app, 'version_string', 'Unknown') if bpy else 'Test Mode'
                    })
            
            # Check if this is a handler message or code execution
            if 'handler' in message:
                # Handle predefined handler functions
                handler_name = message['handler']
                params = message.get('params', {})
                
                try:
                    result = await self.execute_handler(handler_name, params)
                    return json.dumps({
                        'id': message_id,
                        'output': result['output'],
                        'error': result['error'],
                        'result': result.get('result'),  # Structured result data
                        'stream_end': True
                    })
                except Exception as e:
                    return json.dumps({
                        'id': message_id,
                        'output': None,
                        'error': f"Handler '{handler_name}' failed: {str(e)}",
                        'stream_end': True
                    })
            
            elif 'code' in message:
                # Handle direct code execution
                code = message['code']
                stream = message.get('stream', False)
                
                if stream:
                    # Handle streaming execution
                    await self.execute_code_streaming(code, message_id)
                    return ""  # Streaming responses are sent separately
                else:
                    # Handle regular execution
                    result = await self.execute_code(code)
                    
                    return json.dumps({
                        'id': message_id,
                        'output': result['output'],
                        'error': result['error'],
                        'stream_end': True
                    })
            
            else:
                return json.dumps({
                    'id': message_id,
                    'error': 'Missing required field: either "code" or "handler"'
                })
            
        except json.JSONDecodeError as e:
            return json.dumps({
                'error': f'Invalid JSON: {str(e)}',
                'id': None
            })
        except Exception as e:
            return json.dumps({
                'error': f'Internal error: {str(e)}',
                'id': None
            })
    
    async def execute_handler(self, handler_name: str, params: dict) -> dict[str, Any]:
        """Execute a predefined handler function."""
        if not bpy:
            # Test mode - return mock data
            return {
                'output': f"Test mode - handler: {handler_name} with params: {params}",
                'error': None,
                'result': {'test': True, 'handler': handler_name, 'params': params}
            }
        
        # Route to appropriate handler
        if handler_name == 'list_objects':
            result = await self._handle_list_objects(params)
        elif handler_name == 'inspect_addon':
            result = await self._handle_inspect_addon(params)
        elif handler_name == 'reload_addon':
            result = await self._handle_reload_addon(params)
        elif handler_name == 'list_node_groups':
            result = await self._handle_list_node_groups(params)
        elif handler_name == 'get_node_group_info':
            result = await self._handle_get_node_group_info(params)
        else:
            raise ValueError(f"Unknown handler: {handler_name}")
        
        # test json-serializable output
        

        return result

    
    async def _handle_list_objects(self, params: dict) -> dict[str, Any]:
        """Handle list_objects operation."""
        def _do_list():
            object_type = params.get('type')
            only_view_layer = params.get('only_view_layer', False)
            
            # Get all objects in the scene
            if only_view_layer:
                all_objects = bpy.context.view_layer.objects
            else:
                all_objects = list(bpy.context.scene.objects)
            active_object = bpy.context.view_layer.objects.active
            
            objects_data = []
            for obj in all_objects:
                # Check type filter
                if object_type and obj.type != object_type:
                    continue
                
                # Get object information
                obj_info = {
                    "name": obj.name,
                    "type": obj.type,
                    "data_path": f"bpy.data.objects['{obj.name}']",
                    "active": obj == active_object,
                    "visible": obj.visible_get(),
                    "location": list(obj.location)
                }
                objects_data.append(obj_info)
            
            # Create result
            result = {
                "objects": objects_data,
                "total_count": len(objects_data),
                "filtered_type": object_type
            }
            
            return result
        
        fut = task_queue.submit(_do_list)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, fut.result)
        
        return {
            'output': f"Found {len(result['objects'])} objects" + (f" of type {result['filtered_type']}" if result['filtered_type'] else ""),
            'error': None,
            'result': result
        }
    
    async def _handle_get_object_info(self, params: dict) -> dict[str, Any]:
        """Handle get_object_info operation."""
        def _do_get_info():
            object_name = params.get('name')
            if not object_name:
                raise ValueError("Missing required parameter: name")
            
            get_as_evaluated = params.get("get_as_evaluated", False)

            # Find the object
            if get_as_evaluated:
                obj = bpy.context.evaluated_depsgraph_get().objects.get(object_name)
            else:
                obj = bpy.data.objects.get(object_name)
            if not obj:
                raise ValueError(f"Object not found: {object_name}")

            # Get object information
            obj_info = {
                "name": obj.name,
                "type": obj.type,
                "data_path": f"bpy.data.objects['{obj.name}']",
                "active": obj == bpy.context.view_layer.objects.active,
                "visible": obj.visible_get(),
                "location": list(obj.location),
                "rotation": list(obj.rotation_euler),
                "scale": list(obj.scale),
                "dimensions": list(obj.dimensions) if hasattr(obj, 'dimensions') else [],
                "material_slots": {
                    slot.name: {
                        "has_material": bool(slot.material),
                        "material": slot.material.name if slot.material else None,
                    }
                    for slot in obj.material_slots
                } if hasattr(obj, 'material_slots') else {},
                "modifiers": [mod.name for mod in obj.modifiers] if hasattr(obj, 'modifiers') else [],
                "constraints": [con.name for con in obj.constraints] if hasattr(obj, 'constraints') else [],
                "children": [child.name for child in obj.children] if hasattr(obj, 'children') else [],
                "parent": obj.parent.name if obj.parent else None,
                "vertex_groups": [vg.name for vg in obj.vertex_groups] if hasattr(obj, 'vertex_groups') else [],
            }

            def is_vector_attr(attr_name: str) -> bool:
                """Check if the attribute is a vector type."""
                return attr_name in ['location', 'rotation_euler', 'scale', 'dimensions'] or attr_name.endswith('_vector')

            other_attributes = params.get('other_attributes', [])
            obj_info["other_attributes"] = {}
            for attr in other_attributes:
                if hasattr(obj, attr):
                    obj_info["other_attributes"][attr] = (
                        list(getattr(obj, attr))
                        if is_vector_attr(attr)
                        else getattr(obj, attr)
                    )

            return {"object": obj_info}

        fut = task_queue.submit(_do_get_info)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, fut.result)

        return {
            'output': f"Retrieved info for object: {result['object']['name']}",
            'error': None,
            'result': result
        }
    
    async def _handle_get_object_data_info(self, params: dict) -> dict[str, Any]:
        """Handle get_object_data_info operation."""
        def _do_get_info():
            object_name = params.get('name')
            if not object_name:
                raise ValueError("Missing required parameter: name")

            get_as_evaluated = params.get("get_as_evaluated", False)

            # Find the object
            if get_as_evaluated:
                obj = bpy.context.evaluated_depsgraph_get().objects.get(object_name)
            else:
                obj = bpy.data.objects.get(object_name)
            if not obj:
                raise ValueError(f"Object not found: {object_name}")
            
            if not hasattr(obj, 'data'):
                raise ValueError(f"Object '{object_name}' does not have a data block (e.g., mesh, curve, etc.). Is of type '{obj.type}'.")
            
            data = obj.data

            # Get object information
            data_info = {
                "name": data.name,
                "type": data.bl_rna.identifier,
                "data_path": f"bpy.data.objects['{obj.name}'].data",
                "attributes": {
                    attr.name: {
                        "domain": attr.domain,
                        "data_type": attr.data_type,
                        "length": len(attr) if hasattr(attr, '__len__') else None,
                        "is_internal": attr.is_internal if hasattr(attr, 'is_internal') else False,
                        "is_required": attr.is_required if hasattr(attr, 'is_required') else False
                    }
                    for attr in data.attributes
                },
                "materials": [
                    mat.name
                    for mat in data.materials
                ] if data.materials else []
            }

            def is_vector_attr(attr_name: str) -> bool:
                """Check if the attribute is a vector type."""
                return attr_name in ['location', 'rotation_euler', 'scale', 'dimensions'] or attr_name.endswith('_vector')

            other_attributes = params.get('other_attributes', [])
            for attr in other_attributes:
                if hasattr(obj, attr):
                    data_info[attr] = list(getattr(obj, attr)) if is_vector_attr(attr) else getattr(obj, attr)

            return {"data": data_info}

        fut = task_queue.submit(_do_get_info)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, fut.result)

        return {
            'output': f"Retrieved info for object's data: {result['data']['name']}",
            'error': None,
            'result': result
        }
    
    async def _handle_inspect_addon(self, params: dict) -> dict[str, Any]:
        """Handle inspect_addon operation."""
        def _do_inspect():
            import addon_utils
            
            addon_name = params.get('name')
            if not addon_name:
                raise ValueError("Missing required parameter: name")
            
            # Get addon info
            addon = None
            enabled = False
            version = None
            
            # Find the addon
            for mod in addon_utils.modules():
                if mod.__name__ == addon_name or mod.bl_info.get("name") == addon_name:
                    addon = mod
                    enabled = mod.__name__ in bpy.context.preferences.addons
                    version = str(mod.bl_info.get("version", "Unknown"))
                    break
            
            if not addon:
                # Try to find by partial name
                for mod in addon_utils.modules():
                    if addon_name.lower() in mod.__name__.lower() or addon_name.lower() in mod.bl_info.get("name", "").lower():
                        addon = mod
                        enabled = mod.__name__ in bpy.context.preferences.addons
                        version = str(mod.bl_info.get("version", "Unknown"))
                        addon_name = mod.__name__  # Use the actual module name
                        break
            
            # If still not found, return list of all available addons
            if not addon:
                all_addons = []
                for mod in addon_utils.modules():
                    addon_info = {
                        "module_name": mod.__name__,
                        "display_name": mod.bl_info.get("name", mod.__name__),
                        "version": str(mod.bl_info.get("version", "Unknown")),
                        "enabled": mod.__name__ in bpy.context.preferences.addons
                    }
                    all_addons.append(addon_info)
                
                result = {
                    "addon_name": addon_name,
                    "found": False,
                    "enabled": False,
                    "version": None,
                    "operators": [],
                    "classes": [],
                    "keymaps": [],
                    "properties": [f"Addon '{addon_name}' not found. Available addons:"] + [f"- {a['module_name']} ({a['display_name']})" for a in all_addons[:20]]
                }
                return result
            
            # Detailed addon introspection
            operators = []
            classes_info = []
            keymaps = []
            properties = []
            
            try:
                # Find all classes belonging to the addon
                addon_classes = []
                for cls in bpy.types.bpy_struct.__subclasses__():
                    if cls.__module__.startswith(addon_name):
                        addon_classes.append(cls)
                
                for cls in addon_classes:
                    class_info = {
                        "name": cls.__name__,
                        "type": cls.__base__.__name__,
                        "bl_idname": getattr(cls, "bl_idname", None),
                        "bl_label": getattr(cls, "bl_label", None)
                    }
                    classes_info.append(class_info)
                    
                    # Get operators and their details
                    if issubclass(cls, bpy.types.Operator):
                        description = getattr(cls, 'bl_description', '')
                        if not description and cls.__doc__:
                            description = cls.__doc__.strip()
                        
                        op_info = {
                            "bl_idname": cls.bl_idname,
                            "bl_label": getattr(cls, 'bl_label', ''),
                            "bl_description": description,
                            "bl_category": getattr(cls, 'bl_category', None)
                        }
                        operators.append(op_info)
                
                # Find keymaps associated with the addon's operators
                op_idnames = {op['bl_idname'] for op in operators if op['bl_idname']}
                if op_idnames:
                    for km in bpy.context.window_manager.keyconfigs.addon.keymaps:
                        for kmi in km.key_items:
                            if kmi.idname in op_idnames:
                                keymap_info = {
                                    "name": km.name,
                                    "space_type": km.space_type,
                                    "region_type": km.region_type,
                                    "key_items": [f"{kmi.type} ({kmi.value})"]
                                }
                                # Check if this keymap is already added
                                existing_km = next((k for k in keymaps if k['name'] == km.name and k['space_type'] == km.space_type), None)
                                if existing_km:
                                    existing_km['key_items'].append(f"{kmi.type} ({kmi.value})")
                                else:
                                    keymaps.append(keymap_info)
                
                # Get addon preferences/properties
                prefs = bpy.context.preferences.addons.get(addon_name)
                if prefs and hasattr(prefs, 'preferences'):
                    prop_names = [p for p in dir(prefs.preferences) if not p.startswith('_') and not callable(getattr(prefs.preferences, p))]
                    if prop_names:
                        properties.extend(prop_names)
                    else:
                        properties.append("Has preferences object (no direct properties found)")
                
            except Exception as e:
                properties.append(f"Introspection error: {e}")
            
            result = {
                "addon_name": addon_name,
                "found": True,
                "enabled": enabled,
                "version": version,
                "operators": operators,
                "classes": classes_info,
                "keymaps": keymaps,
                "properties": properties
            }
            
            return result
        
        fut = task_queue.submit(_do_inspect)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, fut.result)
        
        found_text = "found" if result['found'] else "not found"
        enabled_text = " (enabled)" if result['enabled'] else " (disabled)" if result['found'] else ""
        
        return {
            'output': f"Addon '{result['addon_name']}' {found_text}{enabled_text}",
            'error': None,
            'result': result
        }
    
    async def _handle_reload_addon(self, params: dict) -> dict[str, Any]:
        """Handle reload_addon operation."""
        def _do_reload():
            import addon_utils
            import sys
            import importlib
            
            addon_name = params.get('name')
            errors = []
            reloaded_modules = []
            addon_found = False
            
            if addon_name:
                # Targeted addon reload
                try:
                    # First try to find and disable the addon
                    for mod in addon_utils.modules():
                        if mod.__name__ == addon_name or mod.bl_info.get("name") == addon_name:
                            addon_name = mod.__name__  # Use actual module name
                            addon_found = True
                            break
                    
                    if not addon_found:
                        # Return list of available addons
                        all_addons = []
                        for mod in addon_utils.modules():
                            addon_info = {
                                "module_name": mod.__name__,
                                "display_name": mod.bl_info.get("name", mod.__name__),
                                "version": str(mod.bl_info.get("version", "Unknown")),
                                "enabled": mod.__name__ in bpy.context.preferences.addons
                            }
                            all_addons.append(addon_info)
                        
                        errors.append(f"Addon '{addon_name}' not found. Available addons:")
                        for addon_info in all_addons[:15]:
                            errors.append(f"- {addon_info['module_name']} ({addon_info['display_name']})")
                    else:
                        # Disable addon if enabled
                        if addon_name in bpy.context.preferences.addons:
                            bpy.ops.preferences.addon_disable(module=addon_name)
                        
                        # Reload the addon module and its submodules
                        modules_to_reload = []
                        for module_name in list(sys.modules.keys()):
                            if module_name.startswith(addon_name):
                                modules_to_reload.append(module_name)
                        
                        for module_name in modules_to_reload:
                            try:
                                if module_name in sys.modules:
                                    importlib.reload(sys.modules[module_name])
                                    reloaded_modules.append(module_name)
                            except Exception as e:
                                errors.append(f"Failed to reload module {module_name}: {str(e)}")
                        
                        # Re-enable addon
                        try:
                            bpy.ops.preferences.addon_enable(module=addon_name)
                        except Exception as e:
                            errors.append(f"Failed to re-enable addon: {str(e)}")
                
                except Exception as e:
                    errors.append(f"Unexpected error: {str(e)}")
                
                result = {
                    "addon_name": addon_name,
                    "found": addon_found,
                    "global_reload": False,
                    "success": len(errors) == 0,
                    "reloaded_modules": reloaded_modules,
                    "errors": errors
                }
            
            else:
                # Global script reload
                try:
                    bpy.ops.script.reload()
                    reloaded_modules = [name for name in sys.modules.keys() if not name.startswith('_')][:20]
                    
                    result = {
                        "addon_name": None,
                        "global_reload": True,
                        "success": True,
                        "reloaded_modules": reloaded_modules,
                        "errors": []
                    }
                
                except Exception as e:
                    result = {
                        "addon_name": None,
                        "global_reload": True,
                        "success": False,
                        "reloaded_modules": [],
                        "errors": [f"Global reload failed: {str(e)}"]
                    }
            
            return result
        
        fut = task_queue.submit(_do_reload)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, fut.result)
        
        if result['global_reload']:
            status = "successful" if result['success'] else "failed"
            output = f"Global script reload {status}"
        else:
            if result['found']:
                status = "successful" if result['success'] else "failed"
                output = f"Addon '{result['addon_name']}' reload {status}"
            else:
                output = f"Addon '{result['addon_name']}' not found"
        
        return {
            'output': output,
            'error': "; ".join(result['errors']) if result['errors'] else None,
            'result': result
        }

    async def _handle_list_node_groups(self, params: dict) -> dict[str, Any]:
        """Handle list_node_groups operation."""
        def _do_list():
            node_groups_data = []
            
            for node_group in bpy.data.node_groups:
                # Get basic node group info
                node_group_info = {
                    "name": node_group.name,
                    "node_tree_type": node_group.type if hasattr(node_group, 'type') else 'UNKNOWN',
                    "node_count": len(node_group.nodes),
                    "inputs": [],
                    "outputs": []
                }
                
                # Get input socket information
                for item in node_group.interface.items_tree:
                    if hasattr(item, 'item_type'):
                        socket_info = {
                            "type": item.socket_type if hasattr(item, 'socket_type') else 'UNKNOWN',
                            "description": item.description if hasattr(item, 'description') else '',
                            "identifier": item.identifier if hasattr(item, 'identifier') else '',
                            "name": item.name if hasattr(item, 'name') else ''
                        }
                        
                        # Add default value if it exists
                        if hasattr(item, 'default_value'):
                            try:
                                # Handle different types of default values
                                default_val = item.default_value
                                if hasattr(default_val, '__len__') and not isinstance(default_val, str):
                                    socket_info["default_value"] = list(default_val)
                                else:
                                    socket_info["default_value"] = default_val
                            except (AttributeError, TypeError):
                                socket_info["default_value"] = None
                        else:
                            socket_info["default_value"] = None
                        
                        if item.item_type == 'SOCKET':
                            if hasattr(item, 'in_out'):
                                if item.in_out == 'INPUT':
                                    node_group_info["inputs"].append(socket_info)
                                elif item.in_out == 'OUTPUT':
                                    node_group_info["outputs"].append(socket_info)
                
                node_groups_data.append(node_group_info)
            
            result = {
                "node_groups": node_groups_data,
                "total_count": len(node_groups_data)
            }
            
            return result
        
        fut = task_queue.submit(_do_list)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, fut.result)
        
        return {
            'output': f"Found {len(result['node_groups'])} node groups",
            'error': None,
            'result': result
        }

    async def _handle_get_node_group_info(self, params: dict) -> dict[str, Any]:
        """Handle get_node_group_info operation."""
        def _do_get_info():
            node_group_name = params.get('name')
            if not node_group_name:
                raise ValueError("Missing required parameter: name")
            
            # Find the node group
            node_group = bpy.data.node_groups.get(node_group_name)
            if not node_group:
                raise ValueError(f"Node group not found: {node_group_name}")
            
            # Get detailed node group information
            nodes_data = []
            
            # Process all nodes in the group
            for node in node_group.nodes:
                node_info = {
                    "name": node.name,
                    "label": node.label,
                    "bl_idname": node.bl_idname,
                    "use_custom_color": getattr(node, 'use_custom_color', False),
                    "color": list(getattr(node, 'color', [0.6, 0.6, 0.6])),
                    "location": list(node.location),
                    "location_absolute": list(getattr(node, 'location_absolute', node.location)),
                    "mute": getattr(node, 'mute', False),
                    "parent": node.parent.name if node.parent else None,
                    "selection_status": getattr(node, 'select', False),
                    "inputs": [],
                    "outputs": [],
                    "properties": []
                }
                
                # Get input sockets
                for input_socket in node.inputs:
                    node_info["inputs"].append(self.process_socket(input_socket))
                
                # Get output sockets
                for output_socket in node.outputs:
                    node_info["outputs"].append(self.process_socket(output_socket))
                
                # Get node properties
                for prop_name in dir(node):
                    if prop_name.startswith('_') or prop_name in ['link', 'links', 'internal_links', 'inputs', 'outputs', 'name', 'label', 'bl_idname', 'location', 'parent', 'select', 'mute', 'color', 'use_custom_color']:
                        continue
                    
                    try:
                        prop_value = getattr(node, prop_name)
                        if callable(prop_value):
                            continue
                        
                        # Get the property's bl_rna type if available
                        prop_type = 'unknown'
                        if hasattr(node.bl_rna.properties, prop_name):
                            prop_rna = node.bl_rna.properties[prop_name]
                            prop_type = prop_rna.type
                        
                        # Convert values to JSON-serializable types
                        if hasattr(prop_value, '__len__') and not isinstance(prop_value, str):
                            try:
                                prop_value = list(prop_value)
                            except (TypeError, ValueError):
                                prop_value = str(prop_value)
                        elif hasattr(prop_value, 'name'):
                            # Handle Blender data types with names
                            prop_value = prop_value.name
                        elif not isinstance(prop_value, (int, float, bool, str, type(None))):
                            prop_value = str(prop_value)
                        
                        prop_info = {
                            "name": prop_name,
                            "bl_rna_type": prop_type,
                            "value": prop_value
                        }
                        node_info["properties"].append(prop_info)
                        
                    except (AttributeError, TypeError, ValueError):
                        # Skip properties that can't be accessed or serialized
                        continue
                
                nodes_data.append(node_info)
            
            result = {
                "node_group_name": node_group_name,
                "node_tree_type": node_group.type if hasattr(node_group, 'type') else 'UNKNOWN',
                "nodes": nodes_data,
                # "links": links_data,
                "total_nodes": len(nodes_data),
                "total_links": len(node_group.links)
            }
            
            return result
        
        fut = task_queue.submit(_do_get_info)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, fut.result)
        
        return {
            'output': f"Retrieved detailed info for node group '{result['node_group_name']}' with {result['total_nodes']} nodes and {result['total_links']} links",
            'error': None,
            'result': result
        }

    async def execute_code(self, code: str) -> dict[str, str | None]:
        """Execute Python code safely and return result."""
        def _do_exec() -> dict[str, str | None]:
            import bmesh
            import mathutils
            
            # Create a comprehensive but secure globals environment
            # Start with most built-ins but exclude dangerous ones
            safe_builtins = {}
            dangerous_builtins = {
                'eval', 'exec', 'compile', 'open', 'input', 'raw_input',
                'file', 'reload', 'exit', 'quit', 'help', 'credits', 'license',
                'copyright', '__loader__', '__spec__', '__package__',
                'delattr', 'setattr'  # Prevent attribute manipulation
            }
            
            # Add all built-ins except the dangerous ones
            import builtins
            for name in dir(builtins):
                if not name.startswith('_') and name not in dangerous_builtins:
                    safe_builtins[name] = getattr(builtins, name)
            
            # Explicitly add some commonly needed ones that might be filtered
            safe_builtins.update({
                'dir': dir,
                'vars': vars,
                'globals': lambda: safe_globals,  # Return our safe globals instead
                'locals': locals,
                'Exception': Exception,
                'ValueError': ValueError,
                'TypeError': TypeError,
                'AttributeError': AttributeError,
                'KeyError': KeyError,
                'IndexError': IndexError,
                'RuntimeError': RuntimeError,
                'NotImplementedError': NotImplementedError,
                'StopIteration': StopIteration,
                '__import__': __import__,  # Allow imports in user code
            })
            
            safe_globals = {
                '__builtins__': safe_builtins,
                'bpy': bpy,
                'bmesh': bmesh,
                'mathutils': mathutils,
            }
            
            # Capture stdout
            import io
            import sys
            
            # old_stdout = sys.stdout
            stdout_capture = io.StringIO()
            sys.stdout = stdout_capture
            
            exec(code, safe_globals)  # noqa: S102 – controlled environment
            return {'output': stdout_capture.getvalue(), 'error': None}

        fut = task_queue.submit(_do_exec)
        # Await from the asyncio thread without blocking the event loop
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, fut.result)
        return result

    async def execute_code_streaming(self, code: str, message_id: str) -> None:
        """Execute Python code with streaming output."""
        if not bpy:
            # Test mode - simulate streaming output
            lines = [f"Test mode - chunk {i}: {code}" for i in range(3)]
            for i, line in enumerate(lines):
                chunk_response = {
                    'id': message_id,
                    'chunk': line,
                    'stream_end': False
                }
                await self.send_response(chunk_response)
                await asyncio.sleep(0.1)  # Simulate processing time
            
            # Send final message
            final_response = {
                'id': message_id,
                'output': "",
                'error': None,
                'stream_end': True
            }
            await self.send_response(final_response)
            return

        try:
            # Import modules outside the restricted environment
            import bmesh
            import mathutils
            
            # Create a comprehensive but secure globals environment
            # Start with most built-ins but exclude dangerous ones
            safe_builtins = {}
            dangerous_builtins = {
                'eval', 'exec', 'compile', 'open', 'input', 'raw_input',
                'file', 'reload', 'exit', 'quit', 'help', 'credits', 'license',
                'copyright', '__loader__', '__spec__', '__package__',
                'delattr', 'setattr'  # Prevent attribute manipulation
            }
            
            # Add all built-ins except the dangerous ones
            import builtins
            for name in dir(builtins):
                if not name.startswith('_') and name not in dangerous_builtins:
                    safe_builtins[name] = getattr(builtins, name)
            
            # Explicitly add some commonly needed ones that might be filtered
            safe_builtins.update({
                'dir': dir,
                'vars': vars,
                'globals': lambda: safe_globals,  # Return our safe globals instead
                'locals': locals,
                'Exception': Exception,
                'ValueError': ValueError,
                'TypeError': TypeError,
                'AttributeError': AttributeError,
                'KeyError': KeyError,
                'IndexError': IndexError,
                'RuntimeError': RuntimeError,
                'NotImplementedError': NotImplementedError,
                'StopIteration': StopIteration,
                '__import__': __import__,  # Allow imports in user code
            })
            
            safe_globals = {
                '__builtins__': safe_builtins,
                'bpy': bpy,
                'bmesh': bmesh,
                'mathutils': mathutils,
            }
            
            # Create streaming stdout capture
            import io
            import sys
            
            old_stdout = sys.stdout
            
            class StreamingCapture(io.StringIO):
                def __init__(self, message_id: str, send_callback):
                    super().__init__()
                    self.message_id = message_id
                    self.send_callback = send_callback
                
                def write(self, s: str) -> int:
                    if s and s.strip():  # Only send non-empty lines
                        chunk_response = {
                            'id': self.message_id,
                            'chunk': s.rstrip('\n'),
                            'stream_end': False
                        }
                        # Schedule the coroutine to run
                        asyncio.create_task(self.send_callback(chunk_response))
                    return len(s)
                
                def flush(self):
                    pass
            
            stdout_capture = StreamingCapture(message_id, self.send_response)
            sys.stdout = stdout_capture
            
            try:
                # Execute the code
                exec(code, safe_globals)
                
                # Send completion message
                final_response = {
                    'id': message_id,
                    'output': "",
                    'error': None,
                    'stream_end': True
                }
                await self.send_response(final_response)
                
            finally:
                sys.stdout = old_stdout
                
        except Exception as e:
            # Send error message
            error_response = {
                'id': message_id,
                'output': None,
                'error': f"{type(e).__name__}: {str(e)}",
                'stream_end': True
            }
            await self.send_response(error_response)

    def _check_json_serializable(self, obj: Any, path: str = "root") -> None:
        """
        Recursively check if an object is JSON serializable.
        
        Args:
            obj: Object to check
            path: Current path in the object structure (for error reporting)
            
        Raises:
            TypeError: If a non-serializable object is found, with detailed path info
        """
        try:
            # Try to serialize just this object to catch issues early
            json.dumps(obj)
        except TypeError:
            # If it fails, we need to find the specific problematic object
            if isinstance(obj, dict):
                for key, value in obj.items():
                    # Check the key itself
                    try:
                        json.dumps(key)
                    except TypeError:
                        raise TypeError(f"Non-serializable key at path '{path}[{repr(key)}]': type {type(key).__name__}")
                    
                    # Recursively check the value
                    self._check_json_serializable(value, f"{path}[{repr(key)}]")
            elif isinstance(obj, (list, tuple)):
                for i, item in enumerate(obj):
                    self._check_json_serializable(item, f"{path}[{i}]")
            elif isinstance(obj, set):
                raise TypeError(f"Non-serializable object at path '{path}': type {type(obj).__name__} (sets are not JSON serializable)")
            else:
                # This is the problematic object
                raise TypeError(f"Non-serializable object at path '{path}': type {type(obj).__name__}")

    async def send_response(self, response: dict) -> None:
        """Send a response message back to the client."""
        try:
            # First check if the response is JSON serializable
            self._check_json_serializable(response)
            
            # If we get here, it should be safe to serialize
            response_str = json.dumps(response)
            response_data = response_str.encode('utf-8')
            response_length = len(response_data).to_bytes(4, byteorder='big')
            
            self.writer.write(response_length + response_data)
            await self.writer.drain()
            
        except TypeError as e:
            # Print detailed error information for debugging
            print(f"BPY MCP: JSON serialization error in send_response: {e}")
            
            # Try to send a fallback error response
            try:
                error_response = {
                    'id': response.get('id', 'unknown'),
                    'output': None,
                    'error': f"JSON serialization error: {str(e)}",
                    'stream_end': True
                }
                
                # Double-check this fallback response is serializable
                fallback_str = json.dumps(error_response)
                fallback_data = fallback_str.encode('utf-8')
                fallback_length = len(fallback_data).to_bytes(4, byteorder='big')
                
                self.writer.write(fallback_length + fallback_data)
                await self.writer.drain()
                
            except Exception as fallback_error:
                print(f"BPY MCP: Failed to send fallback error response: {fallback_error}")
        except Exception as e:
            print(f"BPY MCP: Unexpected error in send_response: {e}")
            traceback.print_exc()

    def process_socket(self, socket: bpy.types.NodeSocket) -> dict:
        """Process a node socket and return its representation."""
        socket_info = {
            "name": socket.name,
            "description": getattr(socket, 'description', ''),
            "type": socket.type,
            "default_value": None,  # Default value will be set later
            "links": []
        }
        
        # Add default value if it exists and is not linked
        if hasattr(socket, 'default_value') and not socket.is_linked:
            try:
                default_val = socket.default_value
                if hasattr(default_val, '__len__') and not isinstance(default_val, str):
                    socket_info["default_value"] = list(default_val)
                else:
                    socket_info["default_value"] = default_val
            except (AttributeError, TypeError):
                pass
                    
        # Get links for this input socket
        for link in socket.links:
            socket_info["links"].append(self.process_node_link(link, is_output=False))
        
        return socket_info

    def process_node_link(self, link: bpy.types.NodeLink, is_output=False) -> dict:
        """Process a node link and return its representation."""
        if is_output:
            return {
                "node": link.from_node.name,
                "socket": {
                    "type": "output",
                    "name": link.from_socket.name
                }
            }
        return {
            "node": link.to_node.name,
            "socket": {
                "type": "input",
                "name": link.to_socket.name
            }
        }

async def start_server_async(host: str = "localhost", port: int = 4777) -> None:
    """Start the async TCP server."""
    global _current_token
    
    # Generate authentication token
    if bpy:
        prefs = bpy.context.preferences.addons[_get_addon_name()].preferences
        if prefs.require_token:
            _current_token = secrets.token_urlsafe(32)
            print(f"BPY MCP: Authentication token: {_current_token}")
        else:
            _current_token = None
    else:
        _current_token = "test-token-123"
    
    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a new client connection."""
        protocol = BPYMCPProtocol(reader, writer)
        await protocol.handle_connection()
    
    server = await asyncio.start_server(handle_client, host, port, reuse_address=True)
    addr = server.sockets[0].getsockname()
    print(f"BPY MCP: Server started on {addr[0]}:{addr[1]}")
        
    async with server:
        await server.serve_forever()


def run_server_in_thread(host: str, port: int) -> None:
    """Run the server in a background thread."""
    global _server_loop
    
    # Create new event loop for this thread
    _server_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_server_loop)
    
    try:
        _server_loop.run_until_complete(start_server_async(host, port))
    except asyncio.CancelledError:
        print("BPY MCP: Server cancelled")
    except Exception as e:
        print(f"BPY MCP: Server error: {e}")
        traceback.print_exc()
    finally:
        _server_loop.close()
        _server_loop = None


def start_server() -> None:
    """Start the BPY MCP server."""
    global _server_thread
    
    if _server_thread and _server_thread.is_alive():
        raise RuntimeError("Server is already running")
    
    # Check Blender's network access permission
    if bpy and not bpy.app.online_access:
        raise RuntimeError(
            "Network access is disabled in Blender. "
            "Enable online access in preferences or start Blender with --enable-online-access"
        )
    
    # Get preferences for host/port
    if bpy:
        prefs = bpy.context.preferences.addons[_get_addon_name()].preferences
        host = prefs.host
        port = prefs.port
    else:
        host = "localhost"
        port = 4777
    
    # Check if port is available
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # allow immediate address reuse to avoid bind errors on reload
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
    except OSError as e:
        raise RuntimeError(f"Cannot bind to {host}:{port} - {e}") from e
    
    # Start server in background thread
    _server_thread = threading.Thread(
        target=run_server_in_thread,
        args=(host, port),
        daemon=True,
        name="BPY-MCP-Server"
    )
    _server_thread.start()
    
    print(f"BPY MCP: Server starting on {host}:{port}")


def stop_server() -> None:
    """Stop the BPY MCP server."""
    global _server_thread, _server_loop, _current_token
    
    if _server_loop:
        # Cancel all tasks in the server loop
        _server_loop.call_soon_threadsafe(_server_loop.stop)
    
    if _server_thread and _server_thread.is_alive():
        _server_thread.join(timeout=5.0)
        if _server_thread.is_alive():
            print("BPY MCP: Warning - server thread did not stop cleanly")
    
    _server_thread = None
    _current_token = None
    
    # Close all connections
    for connection in list(_connections):
        try:
            connection.writer.close()
        except Exception:
            pass
    _connections.clear()
    
    print("BPY MCP: Server stopped")


def is_server_running() -> bool:
    """Check if the server is currently running."""
    return _server_thread is not None and _server_thread.is_alive()


def get_server_info() -> dict[str, Any]:
    """Get information about the current server state."""
    if bpy:
        prefs = bpy.context.preferences.addons[_get_addon_name()].preferences
        host = prefs.host
        port = prefs.port
    else:
        host = "localhost"
        port = 4777
    
    return {
        'running': is_server_running(),
        'host': host,
        'port': port,
        'token': _current_token,
        'connections': len(_connections),
        'blender_version': getattr(bpy.app, 'version_string', 'Unknown') if bpy else 'Test Mode'
    }
