import { Composition } from "remotion";

import {
  CaptionedClip,
  type CaptionedClipProps,
} from "./CaptionedClip";

const DEFAULT_PROPS: CaptionedClipProps = {
  clipPath: "",
  durationSeconds: 8,
  width: 1080,
  height: 1920,
  segments: [],
  style: {
    font: "anton",
    style: "highlight",
    primaryColor: "#FFD700",
    accentColor: "#FFFFFF",
    uppercase: true,
  },
};

export const Root: React.FC = () => {
  return (
    <Composition
      id="CaptionedClip"
      component={CaptionedClip}
      durationInFrames={Math.ceil(DEFAULT_PROPS.durationSeconds * 30)}
      fps={30}
      width={DEFAULT_PROPS.width}
      height={DEFAULT_PROPS.height}
      defaultProps={DEFAULT_PROPS}
      calculateMetadata={({ props }) => ({
        durationInFrames: Math.max(1, Math.ceil(props.durationSeconds * 30)),
        width: props.width,
        height: props.height,
        fps: 30,
      })}
    />
  );
};
