# BPY MCP Addon

A Blender extension that provides a Model Context Protocol (MCP) server for executing Python commands within Blender.

> **Get the MCP server:** [zachEastin/mcp_bpy](https://github.com/zachEastin/mcp_bpy)

## Overview

This extension creates a TCP server that listens for JSON-formatted commands and executes them within the Blender Python environment. It's designed to allow external tools and AI assistants to automate Blender through a standardized protocol.

## Features

- **Network Interface**: TCP server on localhost:4777 (configurable)
- **Authentication**: Optional token-based authentication
- **Secure Execution**: Restricted Python environment for safety
- **Real-time Communication**: JSON-based request/response protocol
- **Blender Integration**: Full access to bpy, bmesh, and mathutils
- **Error Handling**: Comprehensive error reporting and logging

## Installation

### From Source

1. Copy the `bpy_mcp_addon` directory to your Blender extensions folder
2. Open Blender and go to Preferences > Add-ons
3. Find "BPY MCP" in the Development category
4. Enable the extension

### Using Blender Extension Manager

1. Package the extension:
   ```bash
   blender --command extension build bpy_mcp_addon/
   ```
2. Install via Blender's extension manager

## Configuration

The extension can be configured in Blender Preferences > Add-ons > BPY MCP:

- **Host**: Network interface to bind to (default: localhost)
- **Port**: TCP port number (default: 4777)
- **Auto Start**: Automatically start server when Blender starts
- **Require Authentication Token**: Enable/disable authentication

## Security Considerations

⚠️ **Important Security Notes:**

1. **Network Access**: Only bind to localhost (127.0.0.1) unless you understand the security implications
2. **Authentication**: Enable token authentication for production use
3. **Restricted Environment**: The execution environment is sandboxed but not completely secure
4. **Blender Permissions**: Requires Blender's network access to be enabled

## Protocol

### Message Format

Messages are sent as length-prefixed JSON over TCP:

1. 4-byte message length (big-endian unsigned integer)
2. UTF-8 encoded JSON message

### Authentication

```json
{
  "id": "unique_message_id",
  "token": "authentication_token"
}
```

### Code Execution

```json
{
  "id": "unique_message_id",
  "token": "authentication_token",
  "code": "bpy.context.scene.name",
  "stream": true
}
```

### Response Format

```json
{
  "id": "unique_message_id",
  "output": "Scene",
  "error": null,
  "stream_end": true
}
```

## Usage Examples

### Basic Connection Test

```python
import asyncio
import json
import struct

async def send_command(code):
    reader, writer = await asyncio.open_connection('localhost', 4777)
    
    # Authenticate
    auth_msg = {"id": "auth", "token": "your_token_here"}
    await send_json(writer, auth_msg)
    auth_response = await receive_json(reader)
    
    # Execute code
    code_msg = {"id": "exec", "code": code}
    await send_json(writer, code_msg)
    result = await receive_json(reader)
    
    writer.close()
    return result

# Example usage
result = asyncio.run(send_command("print('Hello from Blender!')"))
```

### Blender Automation

```python
# Create a cube
result = await send_command("""
bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
bpy.context.object.name = "MyBlenderCube"
print(f"Created cube: {bpy.context.object.name}")
""")

# Get scene information
result = await send_command("""
scene_info = {
    'name': bpy.context.scene.name,
    'frame_current': bpy.context.scene.frame_current,
    'objects': len(bpy.context.scene.objects)
}
print(f"Scene info: {scene_info}")
""")
```

## Development

### Testing

Run the manual test script:

```bash
cd tests/manual
python test_listener.py
```

### Debugging

1. Check Blender's system console for error messages
2. Enable debug logging in the extension preferences
3. Use `print()` statements in your code for output

### Contributing

1. Follow Blender extension development guidelines
2. Test with multiple Blender versions (4.2+)
3. Ensure network security best practices
4. Document any new features

## Troubleshooting

### Common Issues

1. **"Network access is disabled"**
   - Enable online access in Blender preferences
   - Or start Blender with `--enable-online-access`

2. **"Port already in use"**
   - Change the port number in preferences
   - Check if another instance is running

3. **"Authentication failed"**
   - Check the authentication token
   - Ensure token authentication is properly configured

4. **"Connection refused"**
   - Verify the server is started
   - Check firewall settings
   - Confirm host/port configuration

### Support

- Check the Blender console for error messages
- Review the extension's log output
- Ensure Blender version compatibility (4.2+)

## License

GPL-3.0-or-later

## Changelog

### v0.1.0

- Initial release
- Basic TCP server functionality
- Authentication support
- Blender 4.2+ compatibility
- Secure execution environment
