<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'

function resolveApiBaseUrl() {
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL
  }

  if (typeof window !== 'undefined') {
    return `${window.location.protocol}//${window.location.hostname}:8001`
  }

  return 'http://localhost:8001'
}

const apiBaseUrl = resolveApiBaseUrl()

const state = reactive({
  loading: true,
  saving: false,
  overview: null,
  liveItems: [],
  error: ''
})

const form = reactive({
  prompt_id: 1,
  prompt: 'Generate a personalized recommendation for the lead.'
})

const saveMessage = ref('')
let timerId = null

const latestProbe = computed(() => state.overview?.latest_probe)
const servers = computed(() => state.overview?.servers || [])

async function loadOverview() {
  const [overviewResponse, liveResponse] = await Promise.all([
    fetch(`${apiBaseUrl}/api/overview`),
    fetch(`${apiBaseUrl}/api/quality/live?limit=8`)
  ])

  if (!overviewResponse.ok || !liveResponse.ok) {
    throw new Error('Failed to fetch analytics data')
  }

  const overview = await overviewResponse.json()
  const live = await liveResponse.json()

  state.overview = overview
  state.liveItems = live.items

  Object.assign(form, overview.rag_config)
}

async function refresh() {
  try {
    state.error = ''
    await loadOverview()
  } catch (error) {
    state.error = error.message
  } finally {
    state.loading = false
  }
}

async function saveConfig() {
  state.saving = true
  saveMessage.value = ''

  try {
    const response = await fetch(`${apiBaseUrl}/api/rag/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt_id: Number(form.prompt_id),
        prompt: form.prompt
      })
    })

    if (!response.ok) {
      throw new Error('Failed to update RAG config')
    }

    const result = await response.json()
    saveMessage.value = result.message
    await refresh()
  } catch (error) {
    saveMessage.value = error.message
  } finally {
    state.saving = false
  }
}

async function runProbe() {
  await fetch(`${apiBaseUrl}/api/quality/probe`, { method: 'POST' })
  await refresh()
}

onMounted(async () => {
  await refresh()
  timerId = window.setInterval(refresh, 10000)
})

onUnmounted(() => {
  if (timerId) {
    window.clearInterval(timerId)
  }
})
</script>

<template>
  <main class="page">
    <section class="hero">
      <div>
        <p class="eyebrow">Distributed RAG operations</p>
        <h1>Analytics console for resource monitoring, live probes, and runtime tuning.</h1>
      </div>
      <div class="hero-actions">
        <a
          v-if="state.overview?.grafana_dashboard_url"
          class="button ghost"
          :href="state.overview.grafana_dashboard_url"
          target="_blank"
          rel="noreferrer"
        >
          Open Grafana
        </a>
        <button class="button" @click="runProbe">Run probe</button>
      </div>
    </section>

    <p v-if="state.error" class="error-banner">{{ state.error }}</p>

    <section class="stats-grid" v-if="state.overview">
      <article class="stat-card">
        <span>Probe throughput</span>
        <strong>{{ state.overview.throughput_rpm }} rpm</strong>
      </article>
      <article class="stat-card">
        <span>Success rate</span>
        <strong>{{ Math.round(state.overview.success_rate * 100) }}%</strong>
      </article>
      <article class="stat-card">
        <span>Avg quality</span>
        <strong>{{ state.overview.avg_quality_score }}</strong>
      </article>
      <article class="stat-card">
        <span>Latest latency</span>
        <strong>{{ latestProbe ? `${latestProbe.latency_ms} ms` : 'No data' }}</strong>
      </article>
    </section>

    <section class="layout" v-if="state.overview">
      <article class="panel">
        <div class="panel-head">
          <h2>Server topology</h2>
          <span>{{ servers.length }} targets</span>
        </div>
        <div class="server-list">
          <div class="server-card" v-for="server in servers" :key="server.name">
            <h3>{{ server.name }}</h3>
            <p>{{ server.metrics_url }}</p>
          </div>
        </div>
      </article>

      <article class="panel">
        <div class="panel-head">
          <h2>Runtime RAG config</h2>
          <span>Reload via backend endpoint</span>
        </div>
        <form class="config-form" @submit.prevent="saveConfig">
          <label>
            <span>Prompt ID</span>
            <input v-model="form.prompt_id" type="number" min="1" />
          </label>
          <label class="full">
            <span>Prompt</span>
            <textarea v-model="form.prompt" rows="4"></textarea>
          </label>
          <button class="button full" :disabled="state.saving">
            {{ state.saving ? 'Saving...' : 'Apply config' }}
          </button>
        </form>
        <p class="subtle">{{ saveMessage }}</p>
      </article>

      <article class="panel full-span">
        <div class="panel-head">
          <h2>Grafana resources</h2>
          <span>CPU, memory, disk, and network for three servers</span>
        </div>
        <iframe
          v-if="state.overview.grafana_embed_url"
          class="grafana-frame"
          :src="state.overview.grafana_embed_url"
          title="Grafana dashboard"
        />
        <p v-else class="subtle">Set `GRAFANA_EMBED_URL` in `.env` to embed a dashboard or panel.</p>
      </article>

      <article class="panel full-span">
        <div class="panel-head">
          <h2>Live probe feed</h2>
          <span>Latest synthetic checks against the RAG backend</span>
        </div>
        <div class="probe-table">
          <div class="probe-row probe-header">
            <span>Time</span>
            <span>Status</span>
            <span>Latency</span>
            <span>Quality</span>
            <span>Answer</span>
          </div>
          <div class="probe-row" v-for="item in state.liveItems" :key="item.timestamp">
            <span>{{ new Date(item.timestamp).toLocaleTimeString() }}</span>
            <span :class="item.success ? 'ok' : 'fail'">{{ item.success ? 'ok' : 'fail' }}</span>
            <span>{{ item.latency_ms }} ms</span>
            <span>{{ item.quality_score }}</span>
            <span class="answer">{{ item.answer }}</span>
          </div>
        </div>
      </article>
    </section>
  </main>
</template>
