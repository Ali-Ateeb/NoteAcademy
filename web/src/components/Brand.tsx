import Image from "next/image";

import calculator from "@/assets/brand/calculator.webp";
import compass from "@/assets/brand/compass.webp";
import logoLong from "@/assets/brand/logo-long.webp";
import logoLongWhite from "@/assets/brand/logo-long-white.webp";
import logoStacked from "@/assets/brand/logo-stacked.webp";
import logoStackedWhite from "@/assets/brand/logo-stacked-white.webp";
import pencil from "@/assets/brand/pencil.webp";
import ruler from "@/assets/brand/ruler.webp";

/* Every logo in the app comes through here, so the artwork lives in one place
 * and a re-export of the brand files is a one-directory change. They are
 * static imports on purpose: next/image reads the intrinsic size from the file,
 * so none of these can ever cause layout shift, and the browser is handed
 * a right-sized WebP rather than the 1000×1000 PNG the artwork ships as. */

/** The pencil-and-wordmark lockup — the header and footer logo. Both
 *  colourways are rendered and CSS shows the right one. */
export function LogoLong({
  className = "h-9",
  priority = false,
}: {
  className?: string;
  priority?: boolean;
}) {
  return (
    <>
      <Image
        src={logoLong}
        alt="Note Academy"
        priority={priority}
        sizes="240px"
        className={`logo-on-light w-auto ${className}`}
      />
      <Image
        src={logoLongWhite}
        alt="Note Academy"
        sizes="240px"
        className={`logo-on-dark w-auto ${className}`}
      />
    </>
  );
}

/** The stacked "Note / ACADEMY" mark with the blue underline and pencil.
 *  Both colourways are rendered and CSS shows the right one; the hidden image
 *  is lazy, so a reader only ever downloads the one they can see. */
export function LogoStacked({
  className = "h-28",
  priority = false,
}: {
  className?: string;
  priority?: boolean;
}) {
  return (
    <>
      <Image
        src={logoStacked}
        alt="Note Academy"
        priority={priority}
        sizes="360px"
        className={`logo-on-light w-auto ${className}`}
      />
      <Image
        src={logoStackedWhite}
        alt="Note Academy"
        sizes="360px"
        className={`logo-on-dark w-auto ${className}`}
      />
    </>
  );
}

/** The pencil on its own: small brand mark for empty states, results, loading. */
export function PencilMark({ className = "h-10" }: { className?: string }) {
  return <Image src={pencil} alt="" aria-hidden className={`w-auto ${className}`} />;
}

const STATIONERY = { calculator, compass, pencil, ruler } as const;

/** One piece of desk stationery, bobbing. Decorative, so it has no alt text and
 *  is hidden from assistive tech; the tilt and delay come from the caller so
 *  four of them do not move in unison. */
export function Stationery({
  item,
  className = "",
  delay = 0,
}: {
  item: keyof typeof STATIONERY;
  className?: string;
  delay?: number;
}) {
  return (
    <Image
      src={STATIONERY[item]}
      alt=""
      aria-hidden
      sizes="120px"
      className={`stationery animate-bob pointer-events-none select-none ${className}`}
      style={{ ["--bob-delay" as string]: `${delay}s` }}
    />
  );
}
