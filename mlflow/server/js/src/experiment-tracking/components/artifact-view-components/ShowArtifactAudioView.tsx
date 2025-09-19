import { useEffect, useRef, useState } from 'react';
import WaveSurfer from 'wavesurfer.js';
import { getArtifactBlob, getArtifactLocationUrl } from '../../../common/utils/ArtifactUtils';
import { ArtifactViewErrorState } from './ArtifactViewErrorState';
import { ArtifactViewSkeleton } from './ArtifactViewSkeleton';

const waveSurferStyling = {
  waveColor: '#1890ff',
  progressColor: '#0b3574',
  height: 500,
};

export type ShowArtifactAudioViewProps = {
  runUuid: string;
  path: string;
  getArtifact?: (...args: any[]) => any;
};

const ShowArtifactAudioView = ({ runUuid, path, getArtifact = getArtifactBlob }: ShowArtifactAudioViewProps) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [waveSurfer, setWaveSurfer] = useState<WaveSurfer | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<boolean>(false);

  useEffect(() => {
    if (!containerRef.current) return;

    // Pre-fetch the audio via XHR to include headers (e.g., namespace), then provide an object URL
    let objectUrl: string | undefined;
    const ws = WaveSurfer.create({
      mediaControls: true,
      container: containerRef.current,
      ...waveSurferStyling,
    });

    getArtifactBlob(getArtifactLocationUrl(path, runUuid))
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        ws.load(objectUrl);
      })
      .catch(() => setError(true));

    ws.on('ready', () => {
      setLoading(false);
    });

    ws.on('error', () => {
      setError(true);
    });

    setWaveSurfer(ws);

    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      ws.destroy();
    };
  }, [containerRef, path, runUuid]);

  const showLoading = loading && !error;

  return (
    <div data-testid="audio-artifact-preview">
      {showLoading && <ArtifactViewSkeleton />}
      {error && <ArtifactViewErrorState />}
      {/* This div is always rendered, but its visibility is controlled by the loading and error states */}
      <div
        css={{
          display: loading || error ? 'none' : 'block',
          padding: 20,
        }}
      >
        <div ref={containerRef} />
      </div>
    </div>
  );
};

export default ShowArtifactAudioView;
