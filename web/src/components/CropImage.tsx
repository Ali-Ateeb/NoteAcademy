"use client";

import type { ImgHTMLAttributes } from "react";

import { gatedUrlFor } from "@/lib/cropUrl";

/**
 * A question crop. Behaves as a plain <img>, except that when the file is not
 * in the public bucket (a crop approved a moment ago and not copied yet), it
 * tries the gated `/api/asset` route once instead of showing a broken image.
 * See `lib/cropUrl.ts`.
 */
export function CropImage(props: ImgHTMLAttributes<HTMLImageElement> & { src: string; alt: string }) {
  return (
    // A crop's box is reserved from its aspect ratio; next/image adds nothing here.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      {...props}
      alt={props.alt}
      onError={(event) => {
        const image = event.currentTarget;
        const gated = gatedUrlFor(image.src);
        if (gated && !image.dataset.gated) {
          image.dataset.gated = "1";
          image.src = gated;
        }
        props.onError?.(event);
      }}
    />
  );
}
