"""
KiCad IPC API Client Module

This module provides a Python client for connecting to the KiCad IPC API.
It automatically reads the socket path and token from environment variables
and provides convenient wrapper functions for common API operations.

Usage:
    import kicad_ipc_api
    
    client = kicad_ipc_api.get_client()
    version = client.get_version()
    net_classes = client.get_net_classes()
"""

import os
import socket
import struct
import json
import sys
from typing import Optional, Dict, Any


class KiCadIPCClient:
    """Client for connecting to KiCad IPC API via Unix socket or named pipe."""
    
    def __init__(self):
        self.socket_path = os.environ.get('KICAD_API_SOCKET')
        self.token = os.environ.get('KICAD_API_TOKEN')
        self.sock = None
        self._connected = False
        
    def connect(self) -> bool:
        """
        Connect to the KiCad IPC API socket.
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        if self._connected:
            return True
            
        if not self.socket_path or not self.token:
            print("KiCad IPC API: Missing socket path or token in environment", file=sys.stderr)
            return False
        
        try:
            # Create socket based on platform
            if sys.platform == 'win32':
                # Windows named pipe
                import win32file
                import win32pipe
                
                pipe_name = self.socket_path
                self.sock = win32file.CreateFile(
                    pipe_name,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0, None,
                    win32file.OPEN_EXISTING,
                    0, None
                )
            else:
                # Unix socket (Linux, macOS)
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(self.socket_path)
            
            self._connected = True
            return True
            
        except Exception as e:
            print(f"KiCad IPC API: Connection failed: {e}", file=sys.stderr)
            self.sock = None
            self._connected = False
            return False
    
    def disconnect(self):
        """Close the connection to the IPC API."""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
            self._connected = False
    
    def _send_request(self, request_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Send a request to the IPC API and receive response.
        
        Args:
            request_data: Dictionary containing the API request
            
        Returns:
            Response dictionary or None on error
        """
        if not self._connected and not self.connect():
            return None
        
        try:
            # Serialize request to JSON
            request_json = json.dumps(request_data)
            request_bytes = request_json.encode('utf-8')
            
            # Send length prefix (4 bytes, network byte order)
            length = struct.pack('!I', len(request_bytes))
            
            if sys.platform == 'win32':
                import win32file
                win32file.WriteFile(self.sock, length + request_bytes)
                
                # Read response length
                _, response_length_bytes = win32file.ReadFile(self.sock, 4)
                response_length = struct.unpack('!I', response_length_bytes)[0]
                
                # Read response data
                _, response_bytes = win32file.ReadFile(self.sock, response_length)
            else:
                self.sock.sendall(length + request_bytes)
                
                # Read response length
                response_length_bytes = self._recv_exactly(4)
                if not response_length_bytes:
                    return None
                response_length = struct.unpack('!I', response_length_bytes)[0]
                
                # Read response data
                response_bytes = self._recv_exactly(response_length)
                if not response_bytes:
                    return None
            
            # Parse response
            response_json = response_bytes.decode('utf-8')
            return json.loads(response_json)
            
        except Exception as e:
            print(f"KiCad IPC API: Request failed: {e}", file=sys.stderr)
            self.disconnect()
            return None
    
    def _recv_exactly(self, n: int) -> Optional[bytes]:
        """Receive exactly n bytes from socket."""
        data = b''
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data
    
    def get_version(self) -> Optional[Dict[str, Any]]:
        """
        Get KiCad version information.
        
        Returns:
            Dictionary with version info or None on error
        """
        request = {
            "header": {
                "client_name": "kicad_ipc_api"
            },
            "message": {
                "type_url": "type.googleapis.com/kiapi.common.commands.GetVersion",
                "value": {}
            }
        }
        return self._send_request(request)
    
    def ping(self) -> bool:
        """
        Ping the API server.
        
        Returns:
            True if ping successful, False otherwise
        """
        request = {
            "header": {
                "client_name": "kicad_ipc_api"
            },
            "message": {
                "type_url": "type.googleapis.com/kiapi.common.commands.Ping",
                "value": {}
            }
        }
        response = self._send_request(request)
        return response is not None
    
    def get_net_classes(self) -> Optional[Dict[str, Any]]:
        """
        Get net classes from the current schematic.
        
        Returns:
            Dictionary with net classes or None on error
        """
        request = {
            "header": {
                "client_name": "kicad_ipc_api"
            },
            "message": {
                "type_url": "type.googleapis.com/kiapi.schematic.commands.GetNetClasses",
                "value": {}
            }
        }
        return self._send_request(request)
    
    def commit_schematic_changes(self, action: str, items: list) -> Optional[Dict[str, Any]]:
        """
        Commit schematic changes via the API.
        
        Args:
            action: Action type (e.g., "create", "update", "delete")
            items: List of items to modify
            
        Returns:
            Response dictionary or None on error
        """
        command_type = f"kiapi.schematic.commands.{action.capitalize()}Items"
        
        request = {
            "header": {
                "client_name": "kicad_ipc_api"
            },
            "message": {
                "type_url": f"type.googleapis.com/{command_type}",
                "value": {
                    "items": items,
                    "header": {
                        "document": {
                            "type": "DOCTYPE_SCHEMATIC"
                        }
                    }
                }
            }
        }
        return self._send_request(request)
    
    def get_schematic_data(self, sheet_path: str = "", filters: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """
        Get schematic data from the current document.
        
        Args:
            sheet_path: Optional sheet path to retrieve specific sheet
            filters: Optional filters for what data to include
            
        Returns:
            Dictionary with schematic data or None on error
        """
        if filters is None:
            filters = {}
            
        request = {
            "header": {
                "client_name": "kicad_ipc_api"
            },
            "message": {
                "type_url": "type.googleapis.com/kiapi.schematic.commands.GetItems",
                "value": {
                    "header": {
                        "document": {
                            "type": "DOCTYPE_SCHEMATIC",
                            "sheet_path": sheet_path
                        }
                    },
                    "filters": filters
                }
            }
        }
        return self._send_request(request)
    
    def execute_command(self, command_type: str, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """
        Execute a generic API command.
        
        Args:
            command_type: Fully qualified command type (e.g., "kiapi.common.commands.GetVersion")
            params: Optional parameters dictionary
            
        Returns:
            Response dictionary or None on error
        """
        if params is None:
            params = {}
            
        request = {
            "header": {
                "client_name": "kicad_ipc_api"
            },
            "message": {
                "type_url": f"type.googleapis.com/{command_type}",
                "value": params
            }
        }
        return self._send_request(request)


# Global client instance
_client: Optional[KiCadIPCClient] = None


def get_client() -> KiCadIPCClient:
    """
    Get the global IPC API client instance.
    
    Returns:
        KiCadIPCClient instance
    """
    global _client
    if _client is None:
        _client = KiCadIPCClient()
    return _client


def auto_start():
    """
    Auto-start the IPC client (connect on import).
    This can be called from C++ to establish connection automatically.
    """
    client = get_client()
    if client.connect():
        print("KiCad IPC API: Connected successfully")
    else:
        print("KiCad IPC API: Failed to connect", file=sys.stderr)


# Convenience functions
def get_version() -> Optional[Dict[str, Any]]:
    """Get KiCad version (convenience function)."""
    return get_client().get_version()


def ping() -> bool:
    """Ping the API (convenience function)."""
    return get_client().ping()


def get_net_classes() -> Optional[Dict[str, Any]]:
    """Get net classes (convenience function)."""
    return get_client().get_net_classes()


def commit_schematic_changes(action: str, items: list) -> Optional[Dict[str, Any]]:
    """Commit schematic changes (convenience function)."""
    return get_client().commit_schematic_changes(action, items)


def get_schematic_data(sheet_path: str = "", filters: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
    """Get schematic data (convenience function)."""
    return get_client().get_schematic_data(sheet_path, filters)
