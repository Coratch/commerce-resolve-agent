import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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

/** 返回指定偏好类型的固定可选值，未知类型不提供编辑能力。 */
function optionsFor(memory: PublicCustomerPreference): MemoryValue[] {
  return MEMORY_OPTIONS[memory.memory_type] ?? [];
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
        <span>CONFIRMED MEMORY</span>
        <h1>长期偏好</h1>
        <p>这里只保存你明确确认的低风险沟通偏好，不保存订单或退款事实。</p>
      </header>
      {memories.isPending && <p>正在读取长期偏好…</p>}
      {memories.data?.length === 0 && <p>当前没有已确认偏好。</p>}
      <section className={styles.grid}>
        {memories.data?.map((memory) => (
          <article key={memory.memory_id}>
            <small>{memory.memory_type}</small>
            <select
              aria-label={`修改 ${memory.memory_type}`}
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
                  {value}
                </option>
              ))}
            </select>
            <p>来源 Case：{memory.source_case_id}</p>
            <p>确认时间：{new Date(memory.last_confirmed_at).toLocaleString()}</p>
            <button
              type="button"
              disabled={remove.isPending}
              onClick={() => remove.mutate(memory.memory_id)}
            >
              删除偏好
            </button>
          </article>
        ))}
      </section>
    </main>
  );
}
