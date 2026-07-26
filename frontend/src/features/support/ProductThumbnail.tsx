import { useState } from "react";

import styles from "./Support.module.css";

interface ProductThumbnailProps {
  src?: string | null;
  alt: string;
  size?: "small" | "medium" | "large";
}

const FALLBACK_IMAGE = "/catalog/v1.3/fallback.svg";

/** 展示本地商品图，并在资源缺失时降级到稳定占位图。 */
export function ProductThumbnail({
  src,
  alt,
  size = "medium",
}: ProductThumbnailProps) {
  const [failed, setFailed] = useState(false);
  return (
    <img
      className={`${styles.productThumbnail} ${styles[`productThumbnail${size}`]}`}
      src={!failed && src ? src : FALLBACK_IMAGE}
      alt={alt}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}
