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
                if 'code' not in message:
                    return json.dumps({
                        'id': message_id,
                        'authenticated': True,
                        'blender_version': getattr(bpy.app, 'version_string', 'Unknown') if bpy else 'Test Mode'
                    })
            
            # Execute code
            if 'code' not in message:
                return json.dumps({
                    'id': message_id,
                    'error': 'Missing required field: code'
                })
            
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
    
    async def execute_code(self, code: str) -> dict[str, str | None]:
        """Execute Python code safely and return result."""
        # if not bpy:
        #     # Test mode - just echo the code
        #     return {
        #         'output': f"Test mode - would execute: {code}",
        #         'error': None
        #     }
        
        # try:
        #     # Import modules outside the restricted environment
        #     import bmesh
        #     import mathutils
            
        #     # Create a restricted globals environment
        #     safe_globals = {
        #         '__builtins__': {
        #             # Safe built-ins only
        #             'len': len,
        #             'str': str,
        #             'int': int,
        #             'float': float,
        #             'bool': bool,
        #             'list': list,
        #             'dict': dict,
        #             'tuple': tuple,
        #             'set': set,
        #             'range': range,
        #             'enumerate': enumerate,
        #             'zip': zip,
        #             'sorted': sorted,
        #             'reversed': reversed,
        #             'min': min,
        #             'max': max,
        #             'sum': sum,
        #             'any': any,
        #             'all': all,
        #             'print': print,
        #             '__import__': __import__,  # Allow imports in user code
        #         },
        #         'bpy': bpy,
        #         'bmesh': bmesh,
        #         'mathutils': mathutils,
        #     }
            
        #     # Capture stdout
        #     import io
        #     import sys
            
        #     old_stdout = sys.stdout
        #     stdout_capture = io.StringIO()
        #     sys.stdout = stdout_capture
            
        #     try:
        #         # Execute the code
        #         exec(code, safe_globals)
        #         output = stdout_capture.getvalue()
        #         return {'output': output, 'error': None}
        #     finally:
        #         sys.stdout = old_stdout
                
        # except Exception as e:
        #     error_msg = f"{type(e).__name__}: {str(e)}"
        #     return {'output': None, 'error': error_msg}
        def _do_exec() -> dict[str, str | None]:
            import bmesh
            import mathutils
            
            # Create a restricted globals environment
            safe_globals = {
                '__builtins__': {
                    # Safe built-ins only
                    'len': len,
                    'str': str,
                    'int': int,
                    'float': float,
                    'bool': bool,
                    'list': list,
                    'dict': dict,
                    'tuple': tuple,
                    'set': set,
                    'range': range,
                    'enumerate': enumerate,
                    'zip': zip,
                    'sorted': sorted,
                    'reversed': reversed,
                    'min': min,
                    'max': max,
                    'sum': sum,
                    'any': any,
                    'all': all,
                    'print': print,
                    '__import__': __import__,  # Allow imports in user code
                },
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
            
            # Create a restricted globals environment
            safe_globals = {
                '__builtins__': {
                    # Safe built-ins only
                    'len': len,
                    'str': str,
                    'int': int,
                    'float': float,
                    'bool': bool,
                    'list': list,
                    'dict': dict,
                    'tuple': tuple,
                    'set': set,
                    'range': range,
                    'enumerate': enumerate,
                    'zip': zip,
                    'sorted': sorted,
                    'reversed': reversed,
                    'min': min,
                    'max': max,
                    'sum': sum,
                    'any': any,
                    'all': all,
                    'print': print,
                    '__import__': __import__,  # Allow imports in user code
                },
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

    async def send_response(self, response: dict) -> None:
        """Send a response message back to the client."""
        response_str = json.dumps(response)
        response_data = response_str.encode('utf-8')
        response_length = len(response_data).to_bytes(4, byteorder='big')
        
        self.writer.write(response_length + response_data)
        await self.writer.drain()


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
