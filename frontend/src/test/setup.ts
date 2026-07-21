import "@testing-library/jest-dom/vitest";
import { beforeEach } from "vitest";

class TestStorage implements Storage {
  private readonly values = new Map<string, string>();

  /** 返回当前键数量。 */
  get length(): number {
    return this.values.size;
  }

  /** 删除全部测试键值。 */
  clear(): void {
    this.values.clear();
  }

  /** 读取指定键，不存在时返回 null。 */
  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  /** 按插入顺序读取指定位置的键。 */
  key(index: number): string | null {
    return [...this.values.keys()][index] ?? null;
  }

  /** 删除指定测试键。 */
  removeItem(key: string): void {
    this.values.delete(key);
  }

  /** 写入字符串化测试键值。 */
  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: new TestStorage(),
});

beforeEach(() => {
  localStorage.clear();
});
