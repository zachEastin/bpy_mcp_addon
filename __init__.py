"""
BPY MCP - Model Context Protocol server for Blender automation.

This extension provides a network interface for external tools to execute
Python commands inside Blender through a simple JSON protocol.
"""

import bpy
from bpy.types import AddonPreferences
from bpy.props import BoolProperty, IntProperty, StringProperty

from . import listener, __package__

bl_info = {
    "name": "BPY MCP",
    "description": "Model Context Protocol server for Blender automation",
    "author": "MCP BPY Team",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "System > Preferences > Add-ons",
    "category": "Development",
    "doc_url": "https://github.com/yourusername/mcp_bpy",
    "tracker_url": "https://github.com/yourusername/mcp_bpy/issues",
}


def get_server_running():
    """Dynamically check if the server thread is alive."""
    # Import listener to check server status
    try:
        from . import listener
        return listener.is_server_running()
    except Exception:
        return False


class BPYMCPPreferences(AddonPreferences):
    """Preferences for BPY MCP extension."""
    
    bl_idname = __package__
    
    # Network settings
    port: IntProperty(
        name="Port",
        description="Port number for the MCP server",
        default=4777,
        min=1024,
        max=65535,
    )
    
    host: StringProperty(
        name="Host",
        description="Host address to bind to (localhost recommended for security)",
        default="localhost",
        maxlen=255,
    )
    
    # Security settings
    auto_start: BoolProperty(
        name="Auto Start",
        description="Automatically start the MCP server when Blender starts",
        default=False,
    )
    
    require_token: BoolProperty(
        name="Require Authentication Token",
        description="Require authentication token for connections",
        default=False,
    )
    
    def draw(self, context):
        """Draw the preferences UI."""
        layout = self.layout

        is_server_running = get_server_running()
        
        # Server status
        box = layout.box()
        row = box.row()
        row.label(text="Server Status:", icon='INFO')
        status_icon = 'CHECKMARK' if is_server_running else 'X'
        status_text = "Running" if is_server_running else "Stopped"
        row.label(text=status_text, icon=status_icon)
        
        if not bpy.app.online_access:
            warning_box = layout.box()
            warning_row = warning_box.row()
            warning_row.alert = True
            warning_row.label(text="Warning: Network access is disabled in Blender", icon='ERROR')
            if bpy.app.online_access_overridden:
                warning_row = warning_box.row()
                warning_row.label(text="This was overridden by command line or environment", icon='INFO')
        
        # Network settings
        box = layout.box()
        box.label(text="Network Settings", icon='NETWORK_DRIVE')
        box.prop(self, "host")
        box.prop(self, "port")
        
        # Security settings
        box = layout.box()
        box.label(text="Security Settings", icon='LOCKED')
        box.prop(self, "auto_start")
        box.prop(self, "require_token")
        
        # Controls
        box = layout.box()
        row = box.row(align=True)
        if is_server_running:
            row.operator("bpy_mcp.stop_server", text="Stop Server", icon='PAUSE')
        else:
            row.operator("bpy_mcp.start_server", text="Start Server", icon='PLAY')
        
        row.operator("bpy_mcp.restart_server", text="Restart", icon='FILE_REFRESH')
        
        # Show connection info if running
        if is_server_running:
            info_box = layout.box()
            info_box.label(text="Connection Information", icon='INFO')
            server_url = f"ws://{self.host}:{self.port}"
            info_box.label(text=f"WebSocket URL: {server_url}")
            if hasattr(listener, '_current_token') and listener._current_token:
                info_box.label(text=f"Auth Token: {listener._current_token}")


class BPYMCP_OT_StartServer(bpy.types.Operator):
    """Start the BPY MCP server."""
    
    bl_idname = "bpy_mcp.start_server"
    bl_label = "Start MCP Server"
    bl_description = "Start the Model Context Protocol server"
    
    def execute(self, context):
        """Execute the start server operation."""
        try:
            listener.start_server()
            self.report({'INFO'}, "MCP server started successfully")
            context.area.tag_redraw()  # Redraw the UI to update status
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to start server: {str(e)}")
            return {'CANCELLED'}


class BPYMCP_OT_StopServer(bpy.types.Operator):
    """Stop the BPY MCP server."""
    
    bl_idname = "bpy_mcp.stop_server"
    bl_label = "Stop MCP Server"
    bl_description = "Stop the Model Context Protocol server"
    
    def execute(self, context):
        """Execute the stop server operation."""
        try:
            listener.stop_server()
            self.report({'INFO'}, "MCP server stopped successfully")
            context.area.tag_redraw()  # Redraw the UI to update status
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to stop server: {str(e)}")
            return {'CANCELLED'}


class BPYMCP_OT_RestartServer(bpy.types.Operator):
    """Restart the BPY MCP server."""
    
    bl_idname = "bpy_mcp.restart_server"
    bl_label = "Restart MCP Server"
    bl_description = "Restart the Model Context Protocol server"
    
    def execute(self, context):
        """Execute the restart server operation."""
        try:
            if listener.is_server_running():
                listener.stop_server()
            listener.start_server()
            self.report({'INFO'}, "MCP server restarted successfully")
            context.area.tag_redraw()  # Redraw the UI to update status
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to restart server: {str(e)}")
            return {'CANCELLED'}


class BPYMCP_PT_SidebarPanel(bpy.types.Panel):
    """Sidebar panel for BPY MCP extension."""
    
    bl_label = "BPY MCP"
    bl_idname = "BPY_MCP_PT_sidebar"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Dev"

    def draw(self, context):
        """Draw the sidebar panel."""
        layout = self.layout
        prefs = context.preferences.addons[__package__].preferences
        
        is_server_running = get_server_running()

        # Show server status
        layout.label(text="Server Status:")
        status_icon = 'CHECKMARK' if is_server_running else 'X'
        status_text = "Running" if is_server_running else "Stopped"
        layout.label(text=status_text, icon=status_icon)
        
        # Show connection info if running
        if is_server_running:
            layout.label(text=f"WebSocket URL: ws://{prefs.host}:{prefs.port}")
            if hasattr(listener, '_current_token') and listener._current_token:
                layout.label(text=f"Auth Token: {listener._current_token}")
            layout.operator("bpy_mcp.restart_server", text="Restart Server", icon='FILE_REFRESH')
        else:
            layout.operator("bpy_mcp.start_server", text="Start Server", icon='PLAY')


classes = (
    BPYMCPPreferences,
    BPYMCP_OT_StartServer,
    BPYMCP_OT_StopServer,
    BPYMCP_OT_RestartServer,
    BPYMCP_PT_SidebarPanel,
)


def register():
    """Register the extension classes and start auto-start if enabled."""
    for cls in classes:
        bpy.utils.register_class(cls)
    
    print("BPY MCP extension registered")
    
    # Check if auto-start is enabled
    prefs = bpy.context.preferences.addons[__package__].preferences
    if prefs.auto_start and bpy.app.online_access:
        try:
            listener.start_server()
            print("BPY MCP server auto-started")
        except Exception as e:
            print(f"Failed to auto-start BPY MCP server: {e}")


def unregister():
    """Unregister the extension classes and stop the server."""
    try:
        listener.stop_server()
        print("BPY MCP server stopped during unregister")
    except Exception as e:
        print(f"Error stopping BPY MCP server during unregister: {e}")
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    print("BPY MCP extension unregistered")


if __name__ == "__main__":
    register()
