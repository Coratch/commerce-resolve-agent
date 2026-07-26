import { useEffect, useRef } from "react";

import styles from "./InteractiveField.module.css";

interface InteractiveFieldProps {
  variant?: "customer" | "admin";
}

interface Point {
  orbit: number;
  phase: number;
  speed: number;
  size: number;
}

/** 根据序号构造稳定的视觉粒子，避免每次刷新产生不可复现布局。 */
function createPoints(count: number): Point[] {
  return Array.from({ length: count }, (_, index) => ({
    orbit: 0.12 + ((index * 37) % 70) / 100,
    phase: (index * 2.399963) % (Math.PI * 2),
    speed: 0.000018 + ((index * 13) % 17) * 0.0000012,
    size: 0.8 + ((index * 11) % 9) * 0.16,
  }));
}

/** 绘制对指针产生轻微引力的单循环流场，并在降动效或页面隐藏时暂停。 */
export function InteractiveField({
  variant = "customer",
}: InteractiveFieldProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const targetCanvas = canvas;
    if (
      typeof window.matchMedia !== "function" ||
      typeof ResizeObserver === "undefined"
    ) {
      return;
    }
    let resolvedContext: CanvasRenderingContext2D | null = null;
    try {
      resolvedContext = targetCanvas.getContext("2d");
    } catch {
      return;
    }
    if (resolvedContext === null) return;
    const context = resolvedContext;

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const points = createPoints(variant === "admin" ? 26 : 38);
    let frameId = 0;
    let width = 0;
    let height = 0;
    let pointerX = 0.5;
    let pointerY = 0.45;
    let renderedStaticFrame = false;

    /** 根据容器尺寸和受限 DPR 重建清晰且成本可控的画布。 */
    function resize(): void {
      const bounds = targetCanvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      width = Math.max(1, bounds.width);
      height = Math.max(1, bounds.height);
      targetCanvas.width = Math.round(width * dpr);
      targetCanvas.height = Math.round(height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      renderedStaticFrame = false;
    }

    /** 将指针位置标准化为画布坐标，不修改页面布局。 */
    function trackPointer(event: PointerEvent): void {
      pointerX = Math.min(1, Math.max(0, event.clientX / window.innerWidth));
      pointerY = Math.min(1, Math.max(0, event.clientY / window.innerHeight));
    }

    /** 绘制一帧轨道、连接线和粒子，并只使用 Canvas 像素产生副作用。 */
    function draw(timestamp: number): void {
      context.clearRect(0, 0, width, height);
      const ink =
        variant === "admin" ? "rgba(196, 255, 82, 0.13)" : "rgba(12, 48, 39, 0.10)";
      const signal =
        variant === "admin" ? "rgba(255, 112, 71, 0.36)" : "rgba(255, 91, 52, 0.30)";
      const centerX = width * (0.52 + (pointerX - 0.5) * 0.035);
      const centerY = height * (0.38 + (pointerY - 0.5) * 0.025);

      context.lineWidth = 0.75;
      context.strokeStyle = ink;
      for (const ratio of [0.18, 0.31, 0.46, 0.64]) {
        context.beginPath();
        context.ellipse(
          centerX,
          centerY,
          width * ratio,
          Math.max(40, height * ratio * 0.54),
          -0.12,
          0,
          Math.PI * 2,
        );
        context.stroke();
      }

      const positions = points.map((point) => {
        const motion = reducedMotion.matches ? 0 : timestamp * point.speed;
        const angle = point.phase + motion;
        return {
          x: centerX + Math.cos(angle) * width * point.orbit * 0.52,
          y: centerY + Math.sin(angle) * height * point.orbit * 0.32,
          size: point.size,
        };
      });

      context.strokeStyle = ink;
      for (let index = 1; index < positions.length; index += 4) {
        const previous = positions[index - 1];
        const current = positions[index];
        context.beginPath();
        context.moveTo(previous.x, previous.y);
        context.lineTo(current.x, current.y);
        context.stroke();
      }

      for (const [index, position] of positions.entries()) {
        context.beginPath();
        context.fillStyle = index % 7 === 0 ? signal : ink;
        context.arc(position.x, position.y, position.size, 0, Math.PI * 2);
        context.fill();
      }
    }

    /** 根据页面可见性与动效偏好启动或停止唯一动画循环。 */
    function schedule(timestamp = 0): void {
      if (document.visibilityState !== "visible" || reducedMotion.matches) {
        if (!renderedStaticFrame) {
          draw(timestamp);
          renderedStaticFrame = true;
        }
        frameId = 0;
        return;
      }
      renderedStaticFrame = false;
      draw(timestamp);
      frameId = window.requestAnimationFrame(schedule);
    }

    /** 在可见性或系统动效设置变化后安全重建循环。 */
    function reconcileAnimation(): void {
      if (frameId !== 0) window.cancelAnimationFrame(frameId);
      frameId = 0;
      renderedStaticFrame = false;
      schedule();
    }

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(targetCanvas);
    window.addEventListener("pointermove", trackPointer, { passive: true });
    document.addEventListener("visibilitychange", reconcileAnimation);
    reducedMotion.addEventListener("change", reconcileAnimation);
    resize();
    schedule();

    return () => {
      if (frameId !== 0) window.cancelAnimationFrame(frameId);
      resizeObserver.disconnect();
      window.removeEventListener("pointermove", trackPointer);
      document.removeEventListener("visibilitychange", reconcileAnimation);
      reducedMotion.removeEventListener("change", reconcileAnimation);
    };
  }, [variant]);

  return (
    <canvas
      ref={canvasRef}
      className={`${styles.field} ${styles[variant]}`}
      aria-hidden="true"
      data-testid="interactive-field"
    />
  );
}
