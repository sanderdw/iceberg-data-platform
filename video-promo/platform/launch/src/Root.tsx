import {Composition} from 'remotion';
import {Launch, TOTAL} from './Launch';

export const Root = () => (
  <Composition id="Launch" component={Launch} durationInFrames={TOTAL} fps={30} width={1920} height={1080} />
);
