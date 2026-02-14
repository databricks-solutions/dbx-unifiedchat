import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { Suspense, useState } from 'react';
import { useListGenieRoomsSuspense, useCreateAllGenieRooms, useGetGenieCreationStatusSuspense, useListCreatedGenieRoomsSuspense } from '@/lib/api';
import { selector } from '@/lib/selector';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ArrowLeft, ExternalLink } from 'lucide-react';

export const Route = createFileRoute('/_sidebar/genie-create')({
  component: () => (
    <div>
      <h1 className="text-3xl font-bold mb-6">Create Genie Rooms</h1>
      <Suspense fallback={<CreateSkeleton />}>
        <GenieCreateView />
      </Suspense>
    </div>
  ),
});

function GenieCreateView() {
  const { data: rooms } = useListGenieRoomsSuspense(selector());
  const [creationStarted, setCreationStarted] = useState(false);
  const createAllMutation = useCreateAllGenieRooms();
  const navigate = useNavigate();

  const handleCreateAll = async () => {
    await createAllMutation.mutateAsync();
    setCreationStarted(true);
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Ready to Create {rooms.length} Genie Rooms</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="space-y-2">
              {rooms.map((room) => (
                <div key={room.id} className="flex justify-between items-center p-3 bg-accent rounded-lg">
                  <div>
                    <p className="font-medium">{room.name}</p>
                    <p className="text-sm text-muted-foreground">{room.table_count} tables</p>
                  </div>
                </div>
              ))}
            </div>

            {!creationStarted && (
              <Button onClick={handleCreateAll} disabled={createAllMutation.isPending}>
                {createAllMutation.isPending ? 'Creating...' : 'Create All Rooms'}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {creationStarted && (
        <Suspense fallback={<Skeleton className="h-64 w-full" />}>
          <CreationProgress />
        </Suspense>
      )}

      {creationStarted && (
        <Suspense fallback={<Skeleton className="h-64 w-full" />}>
          <CreatedRooms />
        </Suspense>
      )}

      <div className="flex gap-4">
        <Button variant="outline" onClick={() => navigate({ to: '/genie-builder' })}>
          <ArrowLeft size={16} /> Back
        </Button>
      </div>
    </div>
  );
}

function CreationProgress() {
  const { data: status } = useGetGenieCreationStatusSuspense(selector());

  return (
    <Card>
      <CardHeader>
        <CardTitle>Creation Progress</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {status.rooms.map((room: any) => (
            <div key={room.id} className="flex items-center justify-between p-3 border rounded-lg">
              <span className="font-medium">{room.name}</span>
              <span className={`text-sm px-2 py-1 rounded ${
                room.status === 'created' ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300' :
                room.status === 'creating' ? 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300' :
                room.status === 'failed' ? 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300' :
                'bg-gray-100 text-gray-800'
              }`}>
                {room.status}
              </span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function CreatedRooms() {
  const { data: createdRooms } = useListCreatedGenieRoomsSuspense(selector());

  if (createdRooms.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>✓ Created Genie Rooms</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {createdRooms.map((room) => (
            <div key={room.id} className="border rounded-lg p-4">
              <div className="flex justify-between items-start">
                <div>
                  <h4 className="font-medium mb-1">{room.name}</h4>
                  <p className="text-sm text-muted-foreground">{room.table_count} tables</p>
                </div>
                <a
                  href={room.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 text-sm text-primary hover:underline"
                >
                  Open Genie Space <ExternalLink size={14} />
                </a>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function CreateSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-64 w-full" />
    </div>
  );
}
