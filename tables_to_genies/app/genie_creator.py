"""
Genie room creator module.
Creates Genie spaces via Databricks SDK.
"""
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from typing import List, Dict, Any
import threading


class GenieCreator:
    """Creates Genie rooms from table selections."""
    
    def __init__(self):
        self.config = Config()
        self.client = WorkspaceClient(config=self.config)
        
        # Get SQL warehouse ID
        self.warehouse_id = "a4ed2ccbda385db9"
        
        # Planned rooms
        self.planned_rooms = []
        
        # Creation tracking
        self.creation_status = {
            'status': 'idle',
            'rooms': []
        }
    
    def add_room(self, room_name: str, table_fqns: List[str]) -> Dict[str, Any]:
        """Add a planned Genie room."""
        room_id = f"room-{len(self.planned_rooms) + 1}"
        room = {
            'id': room_id,
            'name': room_name,
            'tables': table_fqns,
            'table_count': len(table_fqns)
        }
        self.planned_rooms.append(room)
        return room
    
    def get_rooms(self) -> List[Dict[str, Any]]:
        """Get all planned rooms."""
        return self.planned_rooms
    
    def delete_room(self, room_id: str) -> bool:
        """Delete a planned room."""
        self.planned_rooms = [r for r in self.planned_rooms if r['id'] != room_id]
        return True
    
    def create_all_rooms(self) -> str:
        """
        Create all planned Genie rooms.
        Returns job_id for status tracking.
        """
        self.creation_status = {
            'status': 'creating',
            'rooms': [
                {
                    **room,
                    'status': 'pending',
                    'space_id': None,
                    'url': None,
                    'error': None
                }
                for room in self.planned_rooms
            ]
        }
        
        # Run creation in background thread
        thread = threading.Thread(target=self._create_rooms_task)
        thread.daemon = True
        thread.start()
        
        return "creation-1"
    
    def _create_rooms_task(self):
        """Background task for creating Genie rooms."""
        try:
            for i, room in enumerate(self.creation_status['rooms']):
                try:
                    self.creation_status['rooms'][i]['status'] = 'creating'
                    
                    # Create Genie space using SDK's create_space method
                    # Documentation: https://databricks-sdk-py.readthedocs.io/en/latest/workspace/genie/spaces.html
                    space = self.client.genie.create_space(
                        display_name=room['name'],
                        description=f"Genie space for {len(room['tables'])} tables"
                    )
                    
                    # Update space with table identifiers
                    self.client.genie.update_space(
                        id=space.id,
                        display_name=room['name'],
                        table_identifiers=room['tables'],
                        sql_warehouse_id=self.warehouse_id
                    )
                    
                    space_id = space.id
                    space_url = f"https://{self.config.host}/sql/genie/{space_id}"
                    
                    self.creation_status['rooms'][i]['status'] = 'created'
                    self.creation_status['rooms'][i]['space_id'] = space_id
                    self.creation_status['rooms'][i]['url'] = space_url
                    
                except Exception as e:
                    self.creation_status['rooms'][i]['status'] = 'failed'
                    self.creation_status['rooms'][i]['error'] = str(e)
                    print(f"Error creating room {room['name']}: {e}")
            
            self.creation_status['status'] = 'completed'
            
        except Exception as e:
            self.creation_status['status'] = 'failed'
            print(f"Error in creation task: {e}")
    
    def get_creation_status(self) -> Dict[str, Any]:
        """Get creation status."""
        return self.creation_status
    
    def get_created_rooms(self) -> List[Dict[str, Any]]:
        """Get all successfully created rooms."""
        if self.creation_status['status'] == 'completed':
            return [r for r in self.creation_status['rooms'] if r['status'] == 'created']
        return []
