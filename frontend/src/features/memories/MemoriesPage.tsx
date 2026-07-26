import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MemoryStick, Trash2 } from "lucide-react";

import { deleteMemory, listMemories, updateMemory } from "../../api/client";
import type {
  MemoryValue,
  PublicCustomerPreference,
  SessionResponse,
} from "../../api/types";
import styles from "./MemoriesPage.module.css";

interface MemoriesPageProps {
  session: SessionResponse;
}

const MEMORY_OPTIONS: Record<string, MemoryValue[]> = {
  preferred_language: ["zh-CN", "en"],
  response_detail: ["concise", "standard", "detailed"],
  communication_tone: ["neutral", "friendly"],
};

const MEMORY_TYPE_LABELS: Record<string, string> = {
  preferred_language: "回复语言",
  response_detail: "回复详细程度",
  communication_tone: "沟通语气",
};

const MEMORY_VALUE_LABELS: Record<MemoryValue, string> = {
  "zh-CN": "中文",
  en: "英文",
  concise: "简洁",
  standard: "标准",
  detailed: "详细",
  neutral: "中性",
  friendly: "友好",
};

/** 返回指定偏好类型的固定可选值，未知类型不提供编辑能力。 */
function optionsFor(memory: PublicCustomerPreference): MemoryValue[] {
  return MEMORY_OPTIONS[memory.memory_type] ?? [];
}

/** 将内部偏好类型转换为客户可以理解的名称。 */
function memoryTypeLabel(memoryType: string): string {
  return MEMORY_TYPE_LABELS[memoryType] ?? "服务偏好";
}

/** 将受限枚举值转换为客户可以理解的中文选项。 */
function memoryValueLabel(value: MemoryValue): string {
  return MEMORY_VALUE_LABELS[value];
}

/** 展示并管理当前账号明确确认的三类长期偏好。 */
export function MemoriesPage({ session }: MemoriesPageProps) {
  const queryClient = useQueryClient();
  const memories = useQuery({
    queryKey: ["memories"],
    queryFn: listMemories,
    enabled: session.mode === "registered",
  });
  const update = useMutation({
    mutationFn: ({ memoryId, value }: { memoryId: string; value: MemoryValue }) =>
      updateMemory(memoryId, value),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["memories"] });
    },
  });
  const remove = useMutation({
    mutationFn: deleteMemory,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["memories"] });
    },
  });

  if (session.mode !== "registered") {
    return <main className={styles.page}>请先通过邀请码注册并登录。</main>;
  }
  return (
    <main className={styles.page}>
      <header>
        <span><MemoryStick aria-hidden="true" size={15} />个性化服务</span>
        <h1>我的服务偏好</h1>
        <p>这里只保存你明确确认的沟通方式，不保存订单、物流或退款事实。</p>
      </header>
      {memories.isPending && <p className={styles.notice}>正在读取服务偏好…</p>}
      {memories.isError && (
        <p className={styles.notice}>暂时无法读取服务偏好，请稍后重试。</p>
      )}
      {memories.data?.length === 0 && (
        <p className={styles.notice}>
          当前没有已保存偏好。智能助手提出建议后，只有你确认才会保存。
        </p>
      )}
      <section className={styles.grid}>
        {memories.data?.map((memory) => (
          <article key={memory.memory_id}>
            <small>已确认偏好</small>
            <h2>{memoryTypeLabel(memory.memory_type)}</h2>
            <select
              aria-label={`修改${memoryTypeLabel(memory.memory_type)}`}
              value={memory.value}
              disabled={update.isPending || optionsFor(memory).length === 0}
              onChange={(event) =>
                update.mutate({
                  memoryId: memory.memory_id,
                  value: event.target.value as MemoryValue,
                })
              }
            >
              {optionsFor(memory).map((value) => (
                <option key={value} value={value}>
                  {memoryValueLabel(value)}
                </option>
              ))}
            </select>
            <p>用于后续智能售后服务，可随时修改或删除。</p>
            <p>最近确认：{new Date(memory.last_confirmed_at).toLocaleString()}</p>
            <button
              type="button"
              disabled={remove.isPending}
              onClick={() => remove.mutate(memory.memory_id)}
            >
              <Trash2 aria-hidden="true" size={15} />
              删除偏好
            </button>
          </article>
        ))}
      </section>
    </main>
  );
}
