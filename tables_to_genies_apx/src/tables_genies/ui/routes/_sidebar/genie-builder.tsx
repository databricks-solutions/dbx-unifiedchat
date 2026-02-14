import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { Suspense, useState } from 'react';
import { useGetGraphDataSuspense, useCreateGenieRoom, useListGenieRoomsSuspense, useDeleteGenieRoom } from '@/lib/api';
import { selector } from '@/lib/selector';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ArrowLeft, X } from 'lucide-react';

export const Route = createFileRoute('/_sidebar/genie-builder')({
  component: () => (
    <div>
      <h1 className="text-3xl font-bold mb-6">Build Genie Rooms</h1>
      <Suspense fallback={<BuilderSkeleton />}>
        <GenieBuilderView />
      </Suspense>
    </div>
  ),
});

function GenieBuilderView() {
  const { data: graphData } = useGetGraphDataSuspense(selector());
  const { data: rooms } = useListGenieRoomsSuspense(selector());
  const [selectedTableFqns, setSelectedTableFqns] = useState<string[]>([]);
  const [roomName, setRoomName] = useState('');
  const createRoomMutation = useCreateGenieRoom();
  const deleteRoomMutation = useDeleteGenieRoom();
  const navigate = useNavigate();

  const tableNodes = graphData.elements.filter((elem: any) => !elem.data.source);

  const handleAddRoom = async () => {
    if (!roomName || selectedTableFqns.length === 0) return;
    
    await createRoomMutation.mutateAsync({
      name: roomName,
      table_fqns: selectedTableFqns,
    });
    
    setRoomName('');
    setSelectedTableFqns([]);
  };

  const handleDeleteRoom = async (roomId: string) => {
    await deleteRoomMutation.mutateAsync(roomId);
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Table Selection */}
        <Card>
          <CardHeader>
            <CardTitle>Select Tables for Room</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-2">Room Name</label>
              <input
                type="text"
                value={roomName}
                onChange={(e) => setRoomName(e.target.value)}
                placeholder="Enter room name..."
                className="w-full px-3 py-2 border rounded-md"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-2">Select Tables</label>
              <select
                multiple
                value={selectedTableFqns}
                onChange={(e) => setSelectedTableFqns(Array.from(e.target.selectedOptions, opt => opt.value))}
                className="w-full h-48 px-3 py-2 border rounded-md"
              >
                {tableNodes.map((node: any) => (
                  <option key={node.data.id} value={node.data.id}>
                    {node.data.id}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground mt-1">Hold Cmd/Ctrl to select multiple</p>
            </div>

            <Button
              onClick={handleAddRoom}
              disabled={!roomName || selectedTableFqns.length === 0 || createRoomMutation.isPending}
            >
              {createRoomMutation.isPending ? 'Adding...' : 'Add Room'}
            </Button>
          </CardContent>
        </Card>

        {/* Planned Rooms */}
        <Card>
          <CardHeader>
            <CardTitle>Planned Rooms ({rooms.length})</CardTitle>
          </CardHeader>
          <CardContent>
            {rooms.length === 0 ? (
              <p className="text-sm text-muted-foreground">No rooms planned yet</p>
            ) : (
              <div className="space-y-2">
                {rooms.map((room) => (
                  <div key={room.id} className="border rounded-lg p-3">
                    <div className="flex justify-between items-start mb-2">
                      <h4 className="font-medium">{room.name}</h4>
                      <Button
                        variant="ghost"
                        onClick={() => handleDeleteRoom(room.id)}
                        className="h-6 w-6 p-0"
                      >
                        <X size={14} />
                      </Button>
                    </div>
                    <p className="text-sm text-muted-foreground">{room.table_count} tables</p>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="flex gap-4">
        <Button variant="outline" onClick={() => navigate({ to: '/graph-explorer' })}>
          <ArrowLeft size={16} /> Back
        </Button>
        <Button onClick={() => navigate({ to: '/genie-create' })} disabled={rooms.length === 0}>
          Next: Create Rooms →
        </Button>
      </div>
    </div>
  );
}

function BuilderSkeleton() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <Skeleton className="h-96 w-full" />
      <Skeleton className="h-96 w-full" />
    </div>
  );
}
