// anja Mission Control — frontend Alpine.js
// Backend: FastAPI a localhost (vedi server.py)

function app() {
  return {
    // ===== STATE =====
    view: 'hub-home',
    theme: 'light',
    skin: '',              // '' | 'swebify' — skin grafico sperimentale
    sidebarCollapsed: false,

    // F-Notify
    notifications: [],
    notifPanelOpen: false,
    notifUnreadCount: 0,
    notifFilter: 'all',        // all | unread | errors | action
    notifScopeCurrent: true,   // true = filtra per workspace corrente, false = tutti
    notifSSE: null,
    notifLoading: false,

    // F-Notify-5 — Active chat streams tracking (multi-tab idle persistence)
    chatActiveStreams: new Set(),

    // F-ResourcesDiscovery — toggle filtro plugin Anja-only vs tutti
    resourcesShowAllPlugins: false,

    // F-Notify-4 — Activity widget (chiuso di default, scelta persistita)
    activityCollapsed: localStorage.getItem('anja.activityCollapsed') !== '0',
    activitySummary: {
      daemons: {}, chat_streaming: [], routines_running: [], goals_running: [],
      totalActive: 0,
    },
    activityPollTimer: null,

    // WS — Research skills settings
    researchSettings: {
      preferred: 'duckduckgo',
      serpapi_configured: false,
      gemini_configured: false,
      message: '',
    },

    // F-DeepResearch — sezione Research (lista task + report viewer)
    research: {
      tasks: [],
      query: '',
      mode: 'standard',
      launching: false,
      message: '',
      current: null,      // {task_id, path, content}
    },
    _researchPollTimer: null,

    // Fase 12b M-Onb 3 — Settings "Profile" tab
    profileSettings: {
      slug: '',
      agentName: 'Anja',
      mode: 'hot',        // 'hot' | 'detail'
      content: '',
      message: '',
    },

    // F-BackupDR Fase 3b — pannello Backup & DR (Settings, admin-only)
    backupDR: {
      loaded: false,
      backups: [],
      running: false,
      message: '', error: false,
      snapshots: [],
      previewRef: '', previewDiff: null, previewChanged: null, previewBusy: false,
      undoBusy: false,
      cardsPreview: null, cardsBusy: false,
      version: null,
      migrateBusy: false,
    },

    // F-Notify-6 — Settings card state
    notifSettings: {
      sources: { goal:true, kanban:true, routine:true, chat:true, script:true,
                 telegram:true, daemon:true, webapp:true, mcp:true },
      min_severity: 0,
      mute_telegram_echo: false,
      auto_cleanup_days: 30,
      saving: false, message: '',
    },

    // Fase 13 — Workspace context switching
    // currentScope: 'hub' (default) | 'project:<name>'
    workspaceScope: 'hub',
    workspaceSwitcherOpen: false,

    // Fase 13 — Project files browser + Fase 4-IDE+ L1 Monaco editor
    projectFiles: {
      project: '',
      cwd: '',           // path relativo dentro project
      entries: [],       // [{name,type,size,modified}]
      selectedFile: '',  // path file aperto
      fileContent: '',   // contenuto originale (server-side, per detect dirty)
      fileSize: 0,
      loading: false,
      error: '',
      // L1 Monaco editor
      editMode: false,
      dirty: false,
      saving: false,
      tooLarge: false,
      currentValue: '',  // current value in the'editor
    },

    // Save diff modal
    saveDiff: {
      open: false,
      path: '',
      originalSize: 0,
      newSize: 0,
    },

    // Fase 22.9 — Hub home recent files (Anja's writes)
    hubRecentFiles: [],
    agentStatus: null,  // F22.9.3 — Anja status card

    // Media gallery (videos+images generati)
    mediaState: {
      items: [],
      filter: 'all',  // 'all' | 'videos' | 'images'
      loading: false,
    },
    projectMedia: {
      items: [],
      loading: false,
      syncing: false,
    },

    // M2/M3 — Office layout state
    activityLog: [],            // [{ts, agent, level, msg}]
    activityBoardConnected: false,
    activityWs: null,           // WebSocket per stream live (M3)

    // Fase 22.9+ — Upload state
    hubUploadBusy: false,
    projectUploadBusy: false,

    // Fase 15 — Kanban
    kanban: {
      tasks: [],
      stats: {},
      includeDone: false,
      mode: 'board',        // 'board' | 'calendar'
      calCursor: null,      // 'YYYY-MM' mese mostrato (null = mese corrente)
      pianoWeekOffset: 0,   // settimana mostrata nel Piano editoriale (0 = settimana corrente)
      ws: null,
      wizardOpen: false,
      wizardBusy: false,
      wizardError: '',
      newTask: {
        title: '', body: '', scope: 'hub', assignee: '', priority: 1,
      },
      detailOpen: false,
      detailTask: null,
      commentInput: '',
      _draggedTask: null,
    },

    // Piano editoriale — fonte = <ws>/data/PIANO.md (NON il kanban).
    pianoItems: [],
    pianoLoading: false,
    pianoEvent: null,     // evento del calendario aperto nel modal
    pianoShowPublished: false,   // elenco: nascondi i pubblicati di default

    // Statistiche workspace (F1c) — fonte = <ws>/data/metrics.db.
    statsData: null,
    statsLoading: false,
    statsTab: 'overview',
    statsRange: 28,
    statsRefreshing: false,
    statsMsg: '',
    socialPerf: { posts: [], total: 0, collected: 0, updated_at: '' },
    socialPerfRefreshing: false,
    socialPerfMsg: '',

    // Connettori workspace (F1a) — fonte = <ws>/.anjawiki/.secrets.env.
    connectors: null,
    connDraft: {},
    connLoading: false,
    connSaving: false,
    connMsg: '',
    connMaterialized: false,

    // Integrazioni hub (connettori condivisi: key modelli immagine) + generazione immagini
    hubConnectors: null,
    hubConnDraft: {},
    hubConnSaving: false,
    hubConnMsg: '',
    mediaGen: { prompt: '', model: '', busy: false, msg: '', models: [] },
    googleOauth: { connected: false, client_configured: false, token_scope: '', redirect_uri: '' },
    gclientSetup: { open: false, msg: '', ok: false },
    googleResources: null,
    audit: { loading: false, products: [], summary: null, msg: '', kind: 'products' },

    // Auth / Identità (F4 Concierge)
    auth: { mode: 'personal', authenticated: false, user: null, has_users: false, ready: false },
    loginForm: { slug: '', password: '', error: '', busy: false },
    identitaUsers: [],
    identitaMsg: '',
    identitaBusy: false,
    newUser: { slug: '', name: '', password: '', role: 'member', error: '' },
    wsAccessList: [],          // workspace interni (gestione membri F4b)
    wsMembers: {},             // ws-name -> [slug]

    // Catalogo workspace attivabili (F5)
    catalogBlueprints: [],
    catalogLoading: false,
    catalogActivating: null,
    catalogForm: { brand: '', backend: '', ecommerce: false, busy: false, error: '' },

    // Catalogo contenuti del sito (workspace marketing) — data/catalogo/*.md
    siteCatalog: { kinds: {}, generated: '', exists: false },
    catalogoTab: '',
    catalogoQuery: '',
    catalogoLoading: false,
    catalogoSyncing: false,
    catalogoMsg: '',

    // Brain personale/condiviso (F3) — note .md libere (Obsidian-interno).
    brainScope: 'user',     // 'user' (personale) | 'hub' (condiviso)
    brainUser: '',
    brainNotes: [],
    brainQuery: '',
    brainOpen: null,        // nota aperta {slug,title,body,links,backlinks}
    brainEditMode: false,
    brainSaving: false,
    brainDraftTitle: '',
    brainDraftBody: '',
    brainGraphOpen: false,
    brainGraph: { w: 760, h: 460, nodes: [], edges: [] },

    // Fase 22.10 — Routines view: toggle "include hub routines" quando in workspace scope
    routinesIncludeHub: false,

    // Fase 22.9 — Hub file browser (Anja's workspace files/data/scripts)
    hubFiles: {
      cwd: '',           // 'files' | 'data' | 'scripts' | sub-path
      entries: [],
      selectedFile: '',
      fileContent: '',
      fileSize: 0,
      loading: false,
      error: '',
      editMode: false,
      dirty: false,
      saving: false,
      currentValue: '',
      tooLarge: false,
    },

    // Fase 22 — Workspace creation wizard
    wsWizard: {
      open: false,
      step: 1,
      name: '',
      ws_type: 'office',
      responsabile_name: '',
      role_description: '',
      responsabile_provider: 'claude',
      responsabile_model: 'sonnet',
      responsabile_effort: '',
      busy: false,
      error: '',
    },

    // Chat-with-file side panel (Fase 4-IDE+ L1.5)
    fileChat: {
      open: false,
      agentScope: 'project',  // 'project' | 'agent:<name>'
      messages: [],            // [{role: 'user'|'assistant'|'tool', content}]
      input: '',
      streaming: false,
      convId: '',
      contextBytes: 0,
      trust: false,            // L1.5.3 — auto-apply senza review modal
      _key: '',
      _pendingReload: false,
      _snapshot: '',           // contenuto pre-edit per Undo
    },

    // Review modal post-hoc (Fase 4-IDE+ L1.5.3)
    fileReview: {
      open: false,
      oldContent: '',
      newContent: '',
      path: '',
    },


    expandedProjects: {},
    sectionsExpanded: {
      projects: true,
      hub: true,
      agents: true,
      routines: true,
      chats: false,
      resources: false,
    },
    expandedAgents: {},
    currentAgentTab: 'overview',           // 'overview' | 'AGENTS.md' | 'SOUL.md' | 'TOOLS.md' | 'CLAUDE.md' | 'sessions' | 'chats'
    agentFileContent: '',                   // markdown raw del file selezionato
    agentSessions: [],
    agentSessionDetail: null,               // {id, content} se selezionata
    agentChatHistory: [],                   // conversazioni filtrate scope=agent:<name>

    currentProject: null,
    currentTab: 'Index',
    currentPage: 'index',
    currentCrossSlug: null,
    currentResourceTab: 'skills',
    currentConversation: null,

    // Fase 7k — mapping web_conv_id → SDK session id (per resume)
    sdkSessionByConv: (() => {
      try { return JSON.parse(localStorage.getItem('anja.sdkSessions') || '{}'); }
      catch (e) { return {}; }
    })(),
    currentRoutineName: null,

    inputText: '',
    messages: [],

    // B3 — /skill /bundle autocomplete
    slashAutocomplete: {
      open: false,
      kind: '',          // 'skill' | 'bundle'
      items: [],         // [{name, description, category, scope}]
      filtered: [],
      index: 0,
      cachedSkills: null,
      cachedBundles: null,
    },

    // dati dal backend
    hubInfo: { name: '', path: '', user: '', lastSync: '' },
    projects: [],
    crossAnalyses: [],
    recentActivity: [],
    routines: [],
    conversations: [],
    health: { errors: 0, warnings: 0, suggestions: 0 },

    // contenuti caricati on-demand
    currentPageContent: '',
    currentCrossContent: '',
    sessionsContent: '',
    pageCache: {},  // {`${proj}/${page}`: content}
    crossCache: {}, // {slug: content}
    resources: { skills: [], plugins: [], mcp: [] },

    // F-RawUI — Sources & Ingest UI
    projectSources: { topics: [] },
    sourcesPending: { files: [], last_updated: 0 },
    sourcesScope: 'project',   // F-HubKnowledge — 'project' | 'hub'
    ingestStatus: {},          // F-HubKnowledge — "topic/filename" → {status, source, error}
    hubWikiPages: [],          // F-HubKnowledge — pagine wiki generate (sources/entities/concepts)
    wikiPreview: { open: false, title: '', content: '', loading: false },
    addSourceModal: { open: false, mode: 'url', topic: 'misc', url: '', filename: '', fileInput: null, submitting: false, error: '', maxPages: 25, ingest: false },
    sourcePreview: { open: false, kind: '', url: '', content: '', topic: '', filename: '' },

    // Fase P-CLI — Printing Press
    ppState: { items: [], loading: false },
    ppDoctorVisible: false,
    ppDoctor: { data: {}, output: '', busy: false },
    ppWizardVisible: false,
    ppWizard: { name: '', source: '', source_type: 'catalog', busy: false, output: '', error: '' },
    ppInstallVisible: false,
    ppInstall: { name: '', scope: 'hub', workspace: '', envText: '', busy: false, error: '', result: '' },
    projectContext: { log_entries: [], sessions: [], conversations: 0 },
    projectContextProject: null,  // tracking last loaded
    projectContextExpanded: true,

    // Routines
    routineDetail: null,             // {name, yaml_text, yaml, state, runs}
    routineDetailLoading: false,

    // Fase 7n — running status live
    routineStatus: { running: false, pid: null, started_at: null, duration_sec: null, dry_run: false, tail: '', tailVisible: false },
    _routineStatusInterval: null,
    routineTriggering: false,
    runLogVisible: false,
    runLogFile: '',
    runLogContent: '',

    // Skill wizard
    skillWizardVisible: false,
    skillWizardMode: 'manual',  // 'manual' | 'import'
    skillSaving: false,
    skillWizardError: '',
    skillForm: { name: '', description: '', scope: 'user-global', body: '' },
    skillImportForm: { url: '', scope: 'user-global', name: '' },

    // MCP wizard
    mcpWizardVisible: false,
    mcpWizardMode: 'stdio',  // 'stdio' | 'remote'
    mcpSaving: false,
    mcpWizardError: '',
    mcpForm: { scope: 'hub', project: '', name: '', command: '', argsText: '', envText: '', type: 'http', url: '', headersText: '' },
    mcpEditing: false,  // Fase 7o: true = edit existing, false = create new
    mcpAi: { description: '', loading: false, candidates: [], notes: '' },
    cloneMcp: { visible: false, saving: false, source: { scope: '', name: '', project: '' }, targetScope: 'hub', targetName: '', envOverrideText: '', error: '' },

    // Skill copy dialog
    copyDialogVisible: false,
    copySaving: false,
    copyError: '',
    copyForm: { name: '', from_scope: '', to_scope: '' },

    // Resource detail modal
    resourceDetailVisible: false,
    resourceDetailTitle: '',
    resourceDetailContent: '',

    // Agents (M-PA 1+2)
    agents: [],
    // Fase 13+ — Project-scope agents (in <project>/.anjawiki/agents/)
    projectAgents: [],
    agentDetail: null,             // {name, path, config, triade, sessions, sessions_count}
    currentAgentName: null,        // agent visualizzato in detail view
    currentAgentChatName: null,    // agent in chat attiva (scope=agent:<name>)
    agentWizardVisible: false,
    agentWizardSaving: false,
    agentWizardError: '',
    agentForm: { name: '', role: '', domain: '', provider: 'claude', model: 'sonnet', effort: 'off', project: '' },
    agentWizardMode: 'ai',  // 'ai' | 'manual' | 'clone'
    // Fase 18.B — Clone state
    agentCloneForm: {
      source_key: '',          // 'hub:trader' o 'project:finanze:trader'
      target_name: '',
      target_project: '',
      include_config: true,
      candidates: [],
    },
    agentAi: { description: '', loading: false, suggestion: null },

    // Fase 14 — Dialectic memory
    dialectic: {
      scope: 'hub',
      slug: '',
      tab: 'active',  // 'active' | 'promoted' | 'decayed' | 'never'
      active: [],
      promoted: [],
      decayed: [],
      never_promote: [],
      file: '',
      exists: false,
      busy: false,
    },

    // Memory Inspector (M-Mem 5)
    memInspect: null,                  // {scope, target, root, triade, tiers, sessions_count, mcp_servers_count}
    memScope: 'hub',                   // 'hub' o 'project'
    memTarget: '',                     // se memScope == 'project'
    memFileSelected: 'AGENTS.md',
    memFileContent: '',
    memFileEditMode: false,
    memFileSaving: false,
    // Fase 12 — User profile editor
    userFileKind: 'hot',           // 'hot' | 'detail'
    userFileContent: '',
    userFileEditMode: false,
    userFileSaving: false,
    userFileSlug: '',              // se vuoto → default_user
    memPreviewPrompt: '',
    memPreview: null,                  // {hot, warm, tokens_estimated, ...}
    memPreviewLoading: false,
    memRegenerating: false,

    // Combobox state — model picker (Fase 7e)
    modelComboHubOpen: false,
    modelComboHubFilter: '',
    modelComboWizardOpen: false,
    modelComboWizardFilter: '',
    filteredModels(provider, q) {
      const list = this.modelsCatalog[provider] || [];
      // Escludi modelli non-chat (image/video/audio/embedding) — non funzionano via opencode run
      const NON_CHAT_RE = /(image|imagine|video|tts|whisper|speech|audio|embedding|moderation|dall-e|stable-diffusion)/i;
      const usable = list.filter(m => !NON_CHAT_RE.test(m));
      const s = (q || '').toLowerCase().trim();
      if (!s) return usable.slice(0, 200);
      return usable.filter(m => m.toLowerCase().includes(s)).slice(0, 200);
    },

    // Settings (Fase 7e — Provider keys)
    settingsState: {
      providers: [],
      secretsPath: '',
      inputs: {},      // {ENV_NAME: "..."} — dati incollati dall'utente
      saving: false,
      message: '',
      error: false,
    },

    // Custom secrets (Fase 7j)
    customSecrets: {
      list: [],
      newKey: '',
      newValue: '',
      message: '',
      error: false,
    },

    // Fase 11 M-Tg — Telegram inbound
    telegramStatus: null,            // {running, enabled, has_token, allowed_chat_ids, ...}
    telegramLink: { code: '', deep_link: '', bot_username: '', expires_at: 0, linked: false, loading: false, _timer: null, _tick: 0 },
    telegramConfig: {
      enabled: false,
      allowed_chat_ids_str: '',
    },

    // Fase 11 — compact conversation
    compactRunning: false,

    // Fase 11 — Realtime voice call (WebRTC + OpenAI ephemeral)
    rtState: {
      active: false,
      connected: false,
      muted: false,
      speaking: false,         // Anja sta parlando
      error: '',
      startedAt: null,
      tickerInterval: null,
      now: Date.now(),
      voice: 'alloy',
      model: '',
      transcript: [],          // [{role:'user'|'assistant', content:''}]
      _pc: null,               // RTCPeerConnection
      _stream: null,           // MediaStream microfono
      _dc: null,               // RTCDataChannel events
      _audioEl: null,          // <audio> element per output
      _currentUserBuf: '',
      _currentAssistantBuf: '',
    },

    // Fase 11 — Hub defaults LLM
    hubDefaults: {
      provider: 'claude',
      model: 'sonnet',
      effort: 'off',
      saving: false,
      message: '',
      modelsFor: {},  // {provider: [{id,...}]}
    },

    // Settings tabs (Fase 11)
    settingsTab: 'providers',  // 'project' | 'providers' | 'audio' | 'integrations' | 'secrets' | 'help'

    // Fase 7t — Ollama local models state
    ollamaState: {
      enabled: false,
      base_url: 'http://localhost:11434',
      online: false,
      models: [],
      statusChecked: false,
      saving: false,
      testing: false,
      refreshing: false,
      error: '',
      message: '',
    },

    // Fase 18.C.4 — Goals matrix dashboard
    goalsMatrix: null,

    // Fase 18.A — Goals state
    goalsState: {
      list: [],
      suggestions: [],
      loading: false,
      filterStatus: 'active',
      judging: '',
      pipelineRunning: false,
      detail: {
        open: false,
        loading: false,
        data: null,
        scope: '',
        id: '',
        reflectText: '',
        linked_tasks: [],
        // M2 — Office collapsibles state
        bodyExpanded: false,
        journalExpanded: true,
        reflectExpanded: false,
        // F4 — Specialist notes
        notesExpanded: true,
        notes: [],
        // Phase B — Pending actions queue
        pendingActions: [],
        pendingPollTimer: null,
        // D2 — Monitor scripts
        scripts: [],
        scriptLog: '',
        scriptLogFile: '',
      },
      wizard: {
        open: false,
        saving: false,
        error: '',
        form: {
          title: '',
          scope: 'hub',
          priority: 'medium',
          deadline: '',
          success_criteria_text: '',
          responsabile: '',
          judge_cron: '0 18 * * 0',
          cron_preset: 'weekly_sun_18',  // preset selector (custom = textarea)
          body_md: '',
        },
        agents: [],  // lista agents disponibili per dropdown responsabile/team
      },
    },

    // M1 — Stepper config wizard goal
    goalWizardSteps: [
      { id: 'obiettivo', label: 'Obiettivo' },
      { id: 'strategia', label: 'Strategia' },
      { id: 'team',      label: 'Team & LLM' },
      { id: 'review',    label: 'Review' },
    ],

    // F22.9.3-bonus — Cron presets per wizard goals
    GOAL_CRON_PRESETS: [
      { id: 'manual',           label: 'Manual only (no auto-judge)', expr: '' },
      { id: 'daily_morning',    label: 'Daily · 09:00',               expr: '0 9 * * *' },
      { id: 'daily_evening',    label: 'Daily · 18:00',               expr: '0 18 * * *' },
      { id: 'every_2h',         label: 'Every 2 hours',                  expr: '0 */2 * * *' },
      { id: 'every_6h',         label: 'Every 6 hours',                  expr: '0 */6 * * *' },
      { id: 'weekly_mon_09',    label: 'Weekly · Mon 09:00',     expr: '0 9 * * 1' },
      { id: 'weekly_sun_18',    label: 'Weekly · Sun 18:00',     expr: '0 18 * * 0' },
      { id: 'weekly_fri_17',    label: 'Weekly · Fri 17:00 (post-week review)', expr: '0 17 * * 5' },
      { id: 'monthly_1st',      label: 'Monthly · 1st of the month 09:00', expr: '0 9 1 * *' },
      { id: 'custom',           label: 'Custom (free cron expression)', expr: '' },
    ],

    // Fase 7v.c — Unified model picker state
    modelPickerOpen: false,
    modelPickerSearch: '',
    unifiedModelGroups: [],     // [{providerId, label, icon, models: []}]
    unifiedModelsRefreshing: false,

    // Fase 7v.b — Anthropic Claude subscription state (detection-only)
    claudeLogin: { pending: false, busy: false, authUrl: '', code: '', msg: '' },
    claudeOauthState: {
      subscription_active: false,
      cli_installed: true,
      account: '',
      api_key_set: false,
      platform: '',
      storage_hint: '',
      precedence: '',
    },

    // Fase 7v — OpenAI ChatGPT subscription state
    openaiOauthState: {
      configured: false,
      account_id_short: '',
      last_refresh: '',
      expired: false,
      supported_models: [],
      anja_enabled: false,
      use_codex_cli: true,
      auth_path: '',
      refreshing: false,
      saving: false,
      error: '',
      message: '',
    },

    // Fase 13+ — AI-suggested questions (project scope)
    suggestedQuestions: {
      project: '',
      questions: [],
      generatedAt: 0,
      loading: false,
      regenerating: false,
    },

    // Fase 13+ — Auto-ingest state
    autoIngest: {
      project: '',
      projectRoot: '',
      config: { enabled: false, mode: 'passive', poll_interval_sec: 30, notify_telegram: false },
      whitelistText: '',
      pending: { files: [] },
      daemon: null,
      saving: false,
      running: false,
      runStatus: '',
    },

    // Fase 13 — Project preferences (override locali per progetto)
    projectPrefs: {
      project: '',
      projectRoot: '',
      default_provider: '',
      default_model: '',
      default_effort: '',
      effective: null,
      saving: false,
      message: '',
    },

    // Audio settings (Fase 11)
    audioConfig: {
      stt: { provider: 'openai', model: 'whisper-1' },
      tts: { provider: 'openai', model: 'tts-1', voice: 'nova' },
      realtime: { provider: 'openai', model: 'gpt-4o-realtime-preview', voice: 'alloy', enabled: false },
      saving: false,
      message: '',
    },

    // Routine wizard
    wizardVisible: false,
    wizardSaving: false,
    wizardError: '',
    wizardForm: {
      name: '',
      description: '',
      scope: 'hub',     // valore canonico: "hub" o "project:<name>"
      schedule: '0 8 * * *',
      provider: 'claude',
      model: 'sonnet',
      effort: 'off',
      prompt: '',
      timeout_sec: 300,
      tags: '',
      enabled: true,
      selectedTools: {},  // {"toolId": true}
      output: [],
    },
    wizardTools: { builtin: [], skills: [], plugins: [], mcp: [] },
    wizardToolsLoading: false,
    wizardCronPresets: [
      { label: 'Daily 08:00', value: '0 8 * * *' },
      { label: 'Daily 09:00', value: '0 9 * * *' },
      { label: 'Mondays 09:00', value: '0 9 * * 1' },
      { label: 'Every 15 min', value: '*/15 * * * *' },
      { label: 'Every 30 min', value: '*/30 * * * *' },
      { label: 'Every hour', value: '0 * * * *' },
      { label: 'First of month', value: '0 0 1 * *' },
    ],

    // CC sessions (Claude Code native sessions del progetto)
    ccSessions: [],
    ccSessionsProject: null,  // tracking last loaded
    ccSessionDetail: null,    // {id, messages, msg_count} se selezionata
    ccSessionLoading: false,

    // Project conversations (chat history specifica del progetto, scope=project:X)
    projectConversations: [],
    projectConversationsProject: null,

    // File tree del progetto
    fileTree: [],
    fileTreeProject: null,
    fileTreeRoot: '',
    fileTreeVisible: true,
    fileTreeExpanded: {},  // {path: bool}

    // loading flags
    loading: {
      registry: true,
      page: false,
      cross: false,
      sessions: false,
      resources: false,
      sources: false,
    },

    // action state
    actionRunning: null,  // null | 'sync' | 'lint-hub' | etc.
    toasts: [],
    _toastSeq: 0,

    // chat / WebSocket state
    ws: null,
    wsConnected: false,
    chatStreaming: false,
    turnTodos: [],          // F-ASP F3: todo del turno in corso (todo.updated)
    aspMode: '',            // F-ASP: permission_mode della sessione (select)
    chatFollow: true,       // F-ASP: la chat segue il fondo (si spegne se l'utente scrolla su)
    thinkingActive: false,  // F-ASP F3: segnale thinking (spinner "ragionando")
    currentConvId: null,
    // F-MultiChatView: split-view — secondo pane che segue/porta avanti una chat in parallelo (live via WS)
    splitView: false,
    secondConvId: null,
    secondMessages: [],
    secondInput: '',
    secondStreaming: false,
    selectedProvider: 'claude',  // 'claude' | 'openai' | 'openrouter' | 'xai'
    selectedModel: 'sonnet',
    selectedEffort: '',  // '' | 'low' | 'medium' | 'high'
    enableImageGen: false,       // Fase 7s + 23 — toggle "consenti gen media (immagini + video)" in chat. Persistente in localStorage.

    // Fase 24 — Chat attachments
    attachments: [],             // [{file_id, filename, category, mime, size_bytes, extracted_text?, extracted_chars?, preview, has_image_b64?}]
    attachmentsUploading: 0,
    dragOver: false,

    // Fase 23.b — Media model selector
    mediaModel: '',              // '' = auto (MCP default), oppure slug es. 'google/veo-3.1-lite'
    mediaPickerOpen: false,
    mediaModelsData: { image: [], video: [] },
    mediaModelsLoaded: false,
    mediaModelsLoading: false,
    // Fase 7t — context window meter
    chatUsage: { tokens: 0, ctx: 0, lastIn: 0, lastOut: 0, cacheRead: 0 },
    get usagePct() {
      if (!this.chatUsage.ctx) return 0;
      return Math.min(100, Math.round((this.chatUsage.tokens / this.chatUsage.ctx) * 100));
    },
    get usageColor() {
      const p = this.usagePct;
      if (p < 50) return 'var(--success)';
      if (p < 75) return '#f59e0b';
      return 'var(--error)';
    },
    formatTokens(n) {
      if (!n) return '0';
      if (n < 1000) return String(n);
      if (n < 1_000_000) return (n / 1000).toFixed(n < 10000 ? 1 : 0) + 'k';
      return (n / 1_000_000).toFixed(1) + 'M';
    },

    // Modelli per provider (Fase 7 multi-LLM)
    modelsCatalog: {
      claude: ['sonnet', 'opus', 'fable', 'haiku'],
      openai: ['gpt-5.5', 'gpt-5.5-pro', 'gpt-5.5-fast', 'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.3-codex'],
      openrouter: [
        'anthropic/claude-haiku-4.5',
        'anthropic/claude-opus-4.5',
        'google/gemini-2.5-pro',
        'google/gemini-2.5-flash',
        'meta-llama/llama-3.3-70b-instruct',
        'deepseek/deepseek-r1',
      ],
      xai: ['grok-3', 'grok-3-mini', 'grok-2'],
    },
    get availableModels() {
      return this.modelsCatalog[this.selectedProvider] || ['sonnet'];
    },
    elapsedSec: 0,
    _elapsedTimer: null,
    _streamingStartTs: 0,
    _lastChatScope: null,  // tracking per resettare messages al cambio scope

    projectTabs: ['Index', 'Overview', 'Entities', 'Concepts', 'Sources', 'Sessions', 'Memory CC', 'Log', 'Chat'],

    // ===== COMPUTED =====
    get currentChatScope() {
      // Agent chat → agent:<name>; project tab → project:<name>;
      // Fase 13 Workspace: se siamo in scope project, default = project:<name>
      // altrimenti hub
      if (this.view === 'chat' && this.currentAgentChatName) {
        return `agent:${this.currentAgentChatName}`;
      }
      if (this.view === 'viewer' && this.currentTab === 'Chat' && this.currentProject) {
        return `project:${this.currentProject}`;
      }
      if (this.workspaceScope && this.workspaceScope.startsWith('project:')) {
        return this.workspaceScope;
      }
      return 'hub';
    },

    get isChatActive() {
      // chat è attiva quando view='chat' OR view='viewer' con tab 'Chat'
      return this.view === 'chat' || (this.view === 'viewer' && this.currentTab === 'Chat');
    },

    get breadcrumb() {
      const hubName = this.hubInfo.name || 'Hub';
      // Fase 22.10 — Workspace prefix in project scope
      const wsName = this.isProjectScope ? this.currentProjectScopeName : null;
      const root = wsName ? [hubName, wsName] : [hubName];

      if (this.view === 'hub-home') return [hubName, 'Home'];
      if (this.view === 'chat' && this.currentAgentChatName) {
        return [...root, 'Chat', `Agent: ${this.currentAgentChatName}`];
      }
      if (this.view === 'chat') return [...root, 'Chat', this.currentConversation ? this.conversations.find(c => c.id === this.currentConversation)?.title : 'New conversation'].filter(Boolean);
      if (this.view === 'viewer') return [hubName, this.currentProject, this.currentTab];
      if (this.view === 'sessions') return [...root, 'Sessions Timeline'];
      if (this.view === 'cross') return [hubName, 'Cross Analyses'];
      if (this.view === 'crossDetail') return [hubName, 'Cross Analyses', this.crossAnalyses.find(c => c.slug === this.currentCrossSlug)?.title];
      if (this.view === 'health') return [hubName, 'Health'];
      if (this.view === 'resources') return [hubName, 'Resources'];
      if (this.view === 'routines') return [...root, 'Routines'];
      if (this.view === 'routineDetail') return [...root, 'Routines', this.currentRoutineName];
      if (this.view === 'memory') {
        const scopeLabel = this.memScope === 'hub' ? 'Hub' : `Project: ${this.memTarget}`;
        return [hubName, 'Memory', scopeLabel];
      }
      if (this.view === 'agents') return [...root, 'Agents'];
      if (this.view === 'agentDetail') return [...root, 'Agents', this.currentAgentName];
      if (this.view === 'hub-files') return [hubName, "Anja's workspace", this.hubFiles.cwd || ''];
      if (this.view === 'project-files') return [hubName, this.projectFiles.project, 'Files'];
      return root;
    },

    // ===== INIT =====
    init() {
      window.app = this;
      console.log('[anja] app() init');
      // F-ASP — follow del fondo chat: lo spegne SOLO lo scroll manuale via dal
      // fondo, lo riaccende tornare al fondo. (capture: scroll non bubbla)
      document.addEventListener('scroll', (e) => {
        const el = e.target;
        if (el && el.classList && el.classList.contains('chat__messages')) {
          this.chatFollow = (el.scrollHeight - el.scrollTop - el.clientHeight) < 80;
        }
      }, true);
      // F-ASP — preferenza permission_mode sticky (vale anche PRIMA della chat)
      try { this.aspMode = localStorage.getItem('anja_asp_mode') || ''; } catch (e) {}
      this.loadAuth();   // F4: bootstrap auth (mostra login se concierge + non autenticato)
      this._applyHubDefaultsToPicker();   // Fase 11: il picker parte dai Hub defaults
      this.loadResearchTasks();           // F-DeepResearch: badge sidebar task in corso
      try {
        this.theme = 'light';   // kit swebify light-only; dark rimosso
        document.documentElement.setAttribute('data-theme', 'light');
      } catch (e) { /* ignore */ }
      // Fase 23 — Restore media gen toggle persistente
      try {
        this.enableImageGen = localStorage.getItem('anja_enable_media_gen') === '1';
      } catch (e) { /* ignore */ }
      // Fase 23.b — Restore media model preference
      try {
        this.mediaModel = localStorage.getItem('anja_media_model') || '';
      } catch (e) { /* ignore */ }
      this.refreshIcons();
      this.$watch('view', (v) => {
        this.refreshIcons();
        if (v === 'hub-home') { this.loadHubRecentFiles(); this.loadGoalsMatrix(); this.loadAnjaStatus(); }  // Fase 22.9 + 18.C.4 + F22.9.3
        if (v === 'sessions') this.loadSessions();
        if (v === 'research') this.loadResearchTasks();
        if (v === 'media') { this.loadMedia(); this.loadMediaModels('hub'); }
        if (v === 'project-media') { this.loadProjectMedia(); this.loadMediaModels(this.currentProjectScopeName); }
        if (v === 'resources') this.loadResources();
        if (v === 'routines') this.loadRoutines();
        if (v === 'agents') {
          // Scope-aware: project scope → project agents only; hub scope → hub agents
          if (this.isProjectScope) this.loadProjectAgents();
          else this.loadAgents();
        }
        if (v === 'memory' && !this.memInspect) this.loadMemInspect();
        if (v === 'chat') {
          if (!this.wsConnected) this.connectWs();
          // Fase 13+ Auto-load suggested questions in project scope
          if (this.isProjectScope) this.loadSuggestedQuestions();
          // Fase 7v.c — assicura unified picker abbia state aggiornato
          this.ensureUnifiedModelsReady();
        }
      });
      // Re-load suggested + project agents when workspace scope cambia
      this.$watch('workspaceScope', () => {
        if (this.view === 'chat' && this.isProjectScope) {
          this.suggestedQuestions.questions = [];
          this.loadSuggestedQuestions();
        }
        // Project agents reload (sempre, indipendentemente dalla view)
        this.loadProjectAgents();
      });
      this.$watch('currentProject', () => this.refreshIcons());
      this.$watch('currentTab', () => { this.refreshIcons(); this.loadCurrentPage(); });
      this.$watch('currentCrossSlug', () => { this.refreshIcons(); this.loadCurrentCross(); });
      this.$watch('expandedProjects', () => this.refreshIcons(), { deep: true });
      this.$watch('sectionsExpanded', () => this.refreshIcons(), { deep: true });
      this.$watch('messages', () => this.refreshIcons());
      this.$watch('sidebarCollapsed', () => this.refreshIcons());
      // M3 — Close activity WS quando detail si chiude
      this.$watch('goalsState.detail.open', (open) => {
        if (!open) {
          this.disconnectActivityWs();
          this.stopPendingActionsPolling();
        }
      });

      // fire-and-forget (no await — Alpine non aspetta promise, ma è OK)
      this.loadRegistry();
      this.loadHealth();
      this.loadConversations();
      this.loadRoutines();
      this.loadAgents();

      // F-Notify: initial fetch + live SSE; refresh anche al cambio scope
      this.loadNotifications();
      this.connectNotifSSE();
      this.startActivityPoll();
      this.$watch('workspaceScope', () => {
        if (this.notifScopeCurrent) this.loadNotifications();
      });

      // Fase 13 Workspace — restore from URL hash + listen changes
      this._restoreWorkspaceFromHash();
      window.addEventListener('hashchange', () => this._restoreWorkspaceFromHash());

      // Fase 4-IDE+ L1.5.4 — global shortcut Cmd+L per toggle chat-with-file
      document.addEventListener('keydown', (ev) => {
        if ((ev.metaKey || ev.ctrlKey) && (ev.key === 'l' || ev.key === 'L')) {
          if (this.view === 'project-files' && this.projectFiles.selectedFile) {
            ev.preventDefault();
            this.toggleFileChat();
          }
        }
      });
      // Restore Trust toggle dal localStorage
      try {
        const t = localStorage.getItem('anja.fileChat.trust');
        if (t === '1') this.fileChat.trust = true;
      } catch (e) {}
      this.$watch('fileChat.trust', (v) => {
        try { localStorage.setItem('anja.fileChat.trust', v ? '1' : '0'); } catch (e) {}
      });
      // Fase 22.10 — Restore routines include-hub toggle
      try {
        if (localStorage.getItem('anja.routinesIncludeHub') === '1') this.routinesIncludeHub = true;
      } catch (e) {}
      this.$watch('routinesIncludeHub', (v) => {
        try { localStorage.setItem('anja.routinesIncludeHub', v ? '1' : '0'); } catch (e) {}
      });
      // Fase 13+ Auto-load project agents se siamo già in project scope al boot
      if (this.isProjectScope) this.loadProjectAgents();
      // Close switcher on click outside
      document.addEventListener('click', (e) => {
        if (this.workspaceSwitcherOpen && !e.target.closest('.workspace-switcher')) {
          this.workspaceSwitcherOpen = false;
        }
      });
    },

    // ===== Fase 13 Workspace — context switching =====

    get isHubScope() {
      return !this.workspaceScope || this.workspaceScope === 'hub';
    },
    get isProjectScope() {
      return typeof this.workspaceScope === 'string' && this.workspaceScope.startsWith('project:');
    },
    get currentProjectScopeName() {
      return this.isProjectScope ? this.workspaceScope.split(':', 2)[1] : '';
    },
    get currentScopeLabel() {
      if (this.isProjectScope) {
        return this.currentProjectScopeName;
      }
      return (this.hubInfo && this.hubInfo.name) || 'Hub';
    },

    // Fase 22 — workspace split by kind
    get internalWorkspaces() {
      return (this.projects || []).filter(p => p.kind === 'internal');
    },
    get externalWorkspaces() {
      return (this.projects || []).filter(p => p.kind !== 'internal');
    },
    // Piano editoriale visibile solo nei workspace marketing (fail-closed)
    get isMarketingWorkspace() {
      if (!this.isProjectScope) return false;
      const p = (this.projects || []).find(p => p.name === this.currentProjectScopeName);
      if (!p) return false;
      return p.type === 'marketing' || (typeof p.blueprint === 'string' && p.blueprint.startsWith('marketing'));
    },
    get wsWizardStepLabel() {
      const s = this.wsWizard.step;
      if (s === 1) return 'Name + type';
      if (s === 2) return 'Responsible agent';
      if (s === 3) return 'Confirm';
      return '';
    },

    get filteredConversations() {
      const list = this.conversations || [];
      if (this.isProjectScope) {
        const projScope = this.workspaceScope;
        return list.filter(c => c.scope === projScope);
      }
      return list.filter(c => !c.scope || c.scope === 'hub' || (c.scope && c.scope.startsWith('agent:')));
    },

    get filteredRoutines() {
      const list = this.routines || [];
      if (this.isProjectScope) {
        const projScope = this.workspaceScope;
        // Fase 22.10 — default solo workspace routine, opzionalmente include hub
        if (this.routinesIncludeHub) {
          return list.filter(r => r.scope === projScope || r.scope === 'hub' || !r.scope);
        }
        return list.filter(r => r.scope === projScope);
      }
      return list.filter(r => !r.scope || r.scope === 'hub' || (r.scope && r.scope.startsWith('agent:')));
    },

    // Fase 22.10 — counts per UI badge
    get routinesHubCount() {
      return (this.routines || []).filter(r => !r.scope || r.scope === 'hub').length;
    },
    get routinesWorkspaceCount() {
      if (!this.isProjectScope) return 0;
      const projScope = this.workspaceScope;
      return (this.routines || []).filter(r => r.scope === projScope).length;
    },

    _restoreWorkspaceFromHash() {
      const h = (window.location.hash || '').replace(/^#\/?/, '').trim();
      // Fase 22 — accetta sia #/project/<name> (legacy) che #/workspace/<name>
      const wsMatch = h.match(/^(?:project|workspace)\/(.+)$/);
      if (wsMatch && wsMatch[1]) {
        this.switchToProject(wsMatch[1], /*pushHash*/ false);
        return;
      }
      if (h.startsWith('project/')) {
        const name = h.substring('project/'.length);
        if (name) {
          this.switchToProject(name, /*pushHash*/ false);
          return;
        }
      }
      // default = hub
      if (this.workspaceScope !== 'hub') {
        this.switchToHub(/*pushHash*/ false);
      }
    },

    _setWorkspaceHash(hash, push = true) {
      if (push && window.location.hash !== hash) {
        history.pushState({}, '', hash);
      } else if (!push) {
        // silent set (during initial restore)
        try { history.replaceState({}, '', hash); } catch (e) {}
      }
    },

    switchToHub(pushHash = true) {
      this.workspaceScope = 'hub';
      this.workspaceSwitcherOpen = false;
      this._setWorkspaceHash('#/hub', pushHash);
      // Refresh icons + reload registry per sicurezza
      this.refreshIcons();
    },

    switchToProject(name, pushHash = true) {
      if (!name) return;
      this.workspaceScope = `project:${name}`;
      this.workspaceSwitcherOpen = false;
      this.currentProject = name;  // hook con state esistente per project pages
      this._setWorkspaceHash(`#/project/${name}`, pushHash);
      // Fase 22 — Auto-set agent al responsabile se workspace internal
      const ws = (this.projects || []).find(p => p.name === name);
      if (ws && ws.kind === 'internal' && ws.responsabile) {
        this.currentAgentChatName = ws.responsabile;
      } else {
        this.currentAgentChatName = null;  // reset: usa default hub agent
      }
      this.openWorkspaceHome();   // entrando in un workspace si atterra sulla sua dashboard
    },

    // Dashboard del workspace (Overview scoped) — stat-card + product-card sul brand
    openWorkspaceHome() {
      this.view = 'workspace-home';
      if (this.loadProjectAgents) this.loadProjectAgents();
      this.loadKanban();
      this.loadProjectMedia();
      this.refreshIcons();
    },
    get wsPlanCount() {
      return (this.kanban.tasks || []).filter(t => (t.tags || []).includes('piano')).length;
    },

    toggleWorkspaceSwitcher() {
      this.workspaceSwitcherOpen = !this.workspaceSwitcherOpen;
      if (this.workspaceSwitcherOpen) this.refreshIcons();
    },

    // ===== F-HubChat — Chiedi ad Anja (chat-driven create) =====
    askAnjaToCreate(kind) {
      // kind: 'workspace' | 'agent' | 'routine' | 'goal'
      this.workspaceSwitcherOpen = false;
      const seeds = {
        workspace: 'I want to create a new workspace. Help me set it up (intent discovery → plan → execute via skill orchestrate-hub).',
        agent: 'I want to create a new specialist agent. Help me set it up (intent discovery → plan → execute via skill orchestrate-hub).',
        routine: 'I want to set up a new scheduled routine. Help me set it up (intent discovery → plan → execute via skill orchestrate-hub).',
        goal: 'I want to define a new goal. Help me set it up with team + judge + success criteria (skill orchestrate-hub).',
      };
      const seed = seeds[kind] || seeds.workspace;
      // Switch a hub scope + view chat, poi inietta prompt seed
      if (this.workspaceScope !== 'hub') this.switchToHub();
      this.view = 'chat';
      this.currentConversation = null;
      this.currentConvId = null;
      this.messages = [];
      // Pre-popola input chat — l'utente vede il prompt e può editarlo prima di inviare
      this.$nextTick(() => {
        this.chatInput = seed;
        if (!this.wsConnected) this.connectWs();
        this.refreshIcons();
        // Focus input se possibile
        const el = document.querySelector('textarea[x-model="chatInput"], input[x-model="chatInput"]');
        if (el) el.focus();
      });
    },

    // ===== Fase 22 — Workspace creation wizard =====
    openWorkspaceWizard() {
      this.workspaceSwitcherOpen = false;
      this.wsWizard = {
        open: true, step: 1,
        name: '', ws_type: 'office',
        responsabile_name: '', role_description: '',
        responsabile_provider: 'claude', responsabile_model: 'sonnet',
        responsabile_effort: '',
        busy: false, error: '',
      };
      this.$nextTick(() => this.refreshIcons());
    },
    closeWorkspaceWizard() {
      this.wsWizard.open = false;
    },
    wizardStepValid() {
      if (this.wsWizard.step === 1) {
        return /^[a-z0-9][a-z0-9-]*$/i.test(this.wsWizard.name || '');
      }
      if (this.wsWizard.step === 2) {
        return !!this.wsWizard.responsabile_name.trim() &&
               !!this.wsWizard.role_description.trim();
      }
      return true;
    },
    wizardNext() {
      if (!this.wizardStepValid()) {
        if (this.wsWizard.step === 1) this.wsWizard.error = 'Workspace name must be alphanumeric (kebab-case)';
        else this.wsWizard.error = 'Fill in the required fields';
        return;
      }
      this.wsWizard.error = '';
      // Auto-fill responsabile_name al passaggio 1→2
      if (this.wsWizard.step === 1 && !this.wsWizard.responsabile_name) {
        this.wsWizard.responsabile_name = 'anja-' + this.wsWizard.name.toLowerCase();
      }
      this.wsWizard.step += 1;
      this.$nextTick(() => this.refreshIcons());
    },
    // ===== Fase 22.9 — Hub home dashboard helpers =====
    async loadHubRecentFiles() {
      try {
        const data = await this.fetchJson('/api/hub/recent-files?limit=5');
        this.hubRecentFiles = (data && data.files) || [];
        this.refreshIcons();
      } catch (e) {
        console.error('[hub] recent files err', e);
        this.hubRecentFiles = [];
      }
    },

    // ===== Workspace media gallery (deliverable da <ws>/files/) =====
    openProjectMedia() {
      this.view = 'project-media';
      this.loadProjectMedia();
      this.refreshIcons();
    },

    async loadProjectMedia() {
      const proj = this.currentProjectScopeName;
      if (!proj) return;
      this.projectMedia.loading = true;
      try {
        const data = await this.fetchJson(`/api/project/media?project=${encodeURIComponent(proj)}`);
        this.projectMedia.items = data?.items || [];
      } catch (e) {
        console.error('[project-media] load err', e);
        this.projectMedia.items = [];
      } finally {
        this.projectMedia.loading = false;
        this.refreshIcons();
      }
    },

    projectMediaTotalBytes() {
      return (this.projectMedia.items || []).reduce((s, i) => s + (i.size_bytes || 0), 0);
    },

    get projectMediaGrouped() {
      const items = this.projectMedia.items || [];
      const groups = {};
      for (const it of items) {
        const d = it.dir || '.';
        if (!groups[d]) groups[d] = [];
        groups[d].push(it);
      }
      return Object.keys(groups).sort().map(dir => ({
        dir,
        label: dir === '.' ? 'files/' : `files/${dir}`,
        items: groups[dir],
      }));
    },

    // ===== Media gallery =====
    async loadMedia() {
      this.mediaState.loading = true;
      try {
        const data = await this.fetchJson(`/api/media/list?kind=${this.mediaState.filter}&limit=500`);
        this.mediaState.items = data?.items || [];
      } catch (e) {
        console.error('[media] load err', e);
        this.mediaState.items = [];
      } finally {
        this.mediaState.loading = false;
        this.refreshIcons();
      }
    },

    mediaTotalBytes() {
      return (this.mediaState.items || []).reduce((s, i) => s + (i.size_bytes || 0), 0);
    },

    get mediaGroupedByDate() {
      const items = this.mediaState.items || [];
      const groups = {};
      for (const it of items) {
        const d = it.date || 'unknown';
        if (!groups[d]) groups[d] = [];
        groups[d].push(it);
      }
      const today = new Date().toISOString().slice(0, 10);
      const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
      const out = [];
      for (const d of Object.keys(groups).sort().reverse()) {
        let label = d;
        if (d === today) label = 'Today';
        else if (d === yesterday) label = 'Yesterday';
        else {
          const days = Math.floor((Date.now() - new Date(d).getTime()) / 86400000);
          if (days > 1 && days < 7) label = `${days} days ago`;
        }
        out.push({ date: d, label, items: groups[d] });
      }
      return out;
    },

    async copyMediaPath(item) {
      try {
        await navigator.clipboard.writeText(item.path);
        this.showToast('success', 'Path copied', item.filename);
      } catch (e) {
        this.showToast('error', 'Copy failed', e.message);
      }
    },

    async openMediaInFinder(item) {
      // Solo macOS — usa endpoint helper se presente, altrimenti copy + suggest
      try {
        await navigator.clipboard.writeText(item.path);
        this.showToast('info', '📁 Path copied', 'Paste in Finder with Cmd+Shift+G');
      } catch (e) {
        this.showToast('error', 'Error', e.message);
      }
    },

    async deleteMedia(item) {
      if (!confirm(`Delete ${item.filename}? Irreversible operation.`)) return;
      try {
        const r = await fetch(`/api/media/${item.kind}/${item.date}/${encodeURIComponent(item.filename)}`, {
          method: 'DELETE',
        });
        if (!r.ok) throw new Error(await r.text());
        this.showToast('success', '🗑 Deleted', item.filename);
        await this.loadMedia();
      } catch (e) {
        this.showToast('error', 'Delete failed', e.message);
      }
    },

    async loadAnjaStatus() {
      // F22.9.3 — Anja status card on hub home
      try {
        const data = await this.fetchJson('/api/hub/anja-status');
        this.agentStatus = data || null;
        this.refreshIcons();
      } catch (e) {
        console.error('[anja-status] err', e);
        this.agentStatus = null;
      }
    },

    async openHubFileByPath(relPath) {
      // path es. "subdir/file.md" relativo a <hub>/files/
      const fullPath = 'files/' + relPath;
      const parts = fullPath.split('/');
      const filename = parts.pop();
      const cwd = parts.join('/');
      this.view = 'hub-files';
      this.hubFiles.cwd = cwd;
      await this.loadHubFilesDir(cwd);
      await this.openHubFile(filename);
    },

    // ===== Fase 15 — Kanban =====
    get kanbanColumns() {
      const cols = [
        { status: 'triage', label: 'Triage' },
        { status: 'todo', label: 'Todo' },
        { status: 'ready', label: 'Ready' },
        { status: 'running', label: 'Running' },
        { status: 'blocked', label: 'Blocked' },
      ];
      if (this.kanban.includeDone) cols.push({ status: 'done', label: 'Done' });
      return cols;
    },

    kanbanByStatus(status) {
      // Card con due_at in ordine cronologico crescente (calendario editoriale);
      // quelle senza data restano in fondo nell'ordine server (priority/created).
      return (this.kanban.tasks || [])
        .filter(t => t.status === status)
        .sort((a, b) => {
          if (a.due_at && b.due_at) return a.due_at < b.due_at ? -1 : (a.due_at > b.due_at ? 1 : 0);
          if (a.due_at) return -1;
          if (b.due_at) return 1;
          return 0;  // ordine server preservato (sort stabile)
        });
    },

    // ===== Kanban: vista calendario + agenda "Programmati" =====
    todayISO() {
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    },

    setKanbanMode(m) {
      this.kanban.mode = m;
      this.refreshIcons();
    },

    get kanbanCalCursor() {
      if (this.kanban.calCursor) return this.kanban.calCursor;
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    },

    get kanbanCalLabel() {
      const [y, m] = this.kanbanCalCursor.split('-').map(Number);
      const names = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno',
        'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre'];
      return `${names[m - 1]} ${y}`;
    },

    calShift(delta) {
      const [y, m] = this.kanbanCalCursor.split('-').map(Number);
      const d = new Date(y, m - 1 + delta, 1);
      this.kanban.calCursor = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
      this.refreshIcons();
    },

    calToday() {
      this.kanban.calCursor = null;
      this.refreshIcons();
    },

    kanbanCalGrid() {
      const [y, m] = this.kanbanCalCursor.split('-').map(Number);
      const first = new Date(y, m - 1, 1);
      const startOffset = (first.getDay() + 6) % 7;   // 0 = lunedì
      const daysInMonth = new Date(y, m, 0).getDate();
      const numWeeks = Math.ceil((startOffset + daysInMonth) / 7);
      // raggruppa i task per giorno (YYYY-MM-DD)
      const byDay = {};
      for (const t of (this.kanban.tasks || [])) {
        if (!t.due_at) continue;
        const day = String(t.due_at).slice(0, 10);
        (byDay[day] = byDay[day] || []).push(t);
      }
      const todayStr = this.todayISO();
      const weeks = [];
      const cur = new Date(y, m - 1, 1 - startOffset);
      for (let w = 0; w < numWeeks; w++) {
        const days = [];
        for (let d = 0; d < 7; d++) {
          const ds = `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, '0')}-${String(cur.getDate()).padStart(2, '0')}`;
          days.push({
            date: ds,
            day: cur.getDate(),
            inMonth: cur.getMonth() === (m - 1),
            today: ds === todayStr,
            tasks: (byDay[ds] || []),
          });
          cur.setDate(cur.getDate() + 1);
        }
        weeks.push(days);
      }
      return weeks;
    },

    // Agenda "Programmati": task con data, non done/archiviati, in ordine cronologico.
    get kanbanScheduled() {
      return (this.kanban.tasks || [])
        .filter(t => t.due_at && !['done', 'archived'].includes(t.status))
        .sort((a, b) => a.due_at < b.due_at ? -1 : (a.due_at > b.due_at ? 1 : 0));
    },

    openKanban() {
      this.view = 'kanban';
      this.loadKanban();
      this._kanbanConnectWs();
      this.refreshIcons();
    },

    // ===== Piano editoriale (F1b): calendario canale×giorno da data/PIANO.md =====
    openPianoEditoriale() {
      this.view = 'piano-editoriale';
      this.loadPiano();           // fonte = <ws>/data/PIANO.md (NON il kanban)
      this.refreshIcons();
    },

    async loadPiano() {
      const proj = this.currentProjectScopeName;
      if (!proj) { this.pianoItems = []; return; }
      this.pianoLoading = true;
      try {
        const data = await this.fetchJson(`/api/project/piano?project=${encodeURIComponent(proj)}`);
        this.pianoItems = data?.items || [];
      } catch (e) {
        console.error('[piano] load err', e);
        this.pianoItems = [];
      } finally {
        this.pianoLoading = false;
        this.refreshIcons();
      }
    },

    // Lunedì (locale) della settimana mostrata, in base a pianoWeekOffset.
    _pianoWeekMonday() {
      const d = new Date();
      d.setHours(0, 0, 0, 0);
      const dow = (d.getDay() + 6) % 7;   // 0 = lunedì
      d.setDate(d.getDate() - dow + this.kanban.pianoWeekOffset * 7);
      return d;
    },

    pianoShift(delta) {
      this.kanban.pianoWeekOffset += delta;
      this.refreshIcons();
    },

    pianoToday() {
      this.kanban.pianoWeekOffset = 0;
      this.refreshIcons();
    },

    get pianoWeekLabel() {
      const mon = this._pianoWeekMonday();
      const sun = new Date(mon); sun.setDate(sun.getDate() + 6);
      const mesi = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic'];
      const a = mon.getDate(), b = sun.getDate();
      return mon.getMonth() === sun.getMonth()
        ? `Settimana ${a}–${b} ${mesi[sun.getMonth()]}`
        : `Settimana ${a} ${mesi[mon.getMonth()]} – ${b} ${mesi[sun.getMonth()]}`;
    },

    // Canale editoriale (blog|instagram|facebook|linkedin) → riga del calendario.
    _CHANNEL_ROWS: [
      { key: 'blog', channel: 'Blog', icon: 'file-text' },
      { key: 'instagram', channel: 'Instagram', icon: 'instagram' },
      { key: 'facebook', channel: 'Facebook', icon: 'facebook' },
      { key: 'linkedin', channel: 'LinkedIn', icon: 'linkedin' },
    ],

    _pianoChannelLabel(ch) {
      return { blog: 'Blog', instagram: 'Instagram', facebook: 'Facebook', linkedin: 'LinkedIn' }[ch] || ch || 'Blog';
    },

    _pianoStatoLabel(stato) {
      return {
        pubblicato: 'Published', bozza: 'Draft', brief: 'Brief',
        idea: 'Idea', repurposed: 'Repurposed',
      }[stato] || stato;
    },

    openPianoEvent(ev) { this.pianoEvent = ev; this.refreshIcons(); },
    closePianoEvent() { this.pianoEvent = null; },

    // Etichetta breve della pill nel calendario (~22 char).
    _pianoEvLabel(title) {
      const s = (title || '(senza titolo)').trim();
      return s.length > 22 ? s.slice(0, 21) + '…' : s;
    },

    // Matrice canale × giorno della settimana corrente, da pianoItems (PIANO.md).
    pianoGrid() {
      const wd = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'];
      const todayStr = this.todayISO();
      const mon = this._pianoWeekMonday();
      const days = [];
      for (let i = 0; i < 7; i++) {
        const d = new Date(mon); d.setDate(d.getDate() + i);
        const ds = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        days.push({ date: ds, wd: wd[i], day: d.getDate(), today: ds === todayStr });
      }

      // index eventi per (canale, giorno)
      const byKey = {};
      let idx = 0;
      for (const it of (this.pianoItems || [])) {
        if (!it.date) continue;
        const day = String(it.date).slice(0, 10);
        if (!days.some(d => d.date === day)) continue;   // fuori settimana
        const ch = it.channel || 'blog';
        const key = ch + '|' + day;
        (byKey[key] = byKey[key] || []).push({
          id: `${ch}-${day}-${idx++}`,
          title: it.title || '',
          label: this._pianoEvLabel(it.title),
          stato: it.status || 'idea',
          channel: this._pianoChannelLabel(ch),
          date: day,
          keyword: it.keyword || '',
          kind: ch === 'blog' ? 'Articolo' : 'Post social',
        });
      }

      const rows = this._CHANNEL_ROWS.map(c => ({
        channel: c.channel,
        icon: c.icon,
        cells: days.map(d => ({
          date: d.date,
          today: d.today,
          events: byKey[c.key + '|' + d.date] || [],
        })),
      }));

      return { days, rows };
    },

    // Stati editoriali selezionabili nella lista (write-back → PIANO.md).
    _PIANO_STATI: ['idea', 'brief', 'bozza', 'pubblicato', 'repurposed'],

    // Lista del piano: tutti gli item (con/senza data) ordinati per data.
    get pianoPublishedCount() {
      return (this.pianoItems || []).filter(it => (it.status || '') === 'pubblicato').length;
    },

    get pianoList() {
      return (this.pianoItems || [])
        .filter(it => this.pianoShowPublished || (it.status || '') !== 'pubblicato')
        .map((it, i) => ({
          id: `piano-${i}`,
          idx: i,
          title: it.title || '(senza titolo)',
          kw: (it.keyword || '').slice(0, 90),
          stato: it.status || 'idea',
          statoLabel: this._pianoStatoLabel(it.status || 'idea'),
          channel: this._pianoChannelLabel(it.channel),
          channelKey: it.channel || 'blog',
          due: it.date ? String(it.date).slice(0, 10) : '',
          kind: it.kind || 'blog',
          anchor: it.anchor || '',
        }))
        .sort((a, b) => {
          if (a.due && b.due) return a.due < b.due ? -1 : (a.due > b.due ? 1 : 0);
          if (a.due) return -1;
          if (b.due) return 1;
          return 0;
        });
    },

    // Write-back stato di un item → data/PIANO.md, poi ricarica la vista.
    async setPianoStatus(row, status) {
      const proj = this.currentProjectScopeName;
      if (!proj || !row || !row.anchor || status === row.stato) return;
      // ottimistico: aggiorna subito l'item sorgente (più item social condividono anchor)
      for (const it of (this.pianoItems || [])) {
        if ((it.kind || 'blog') === row.kind && (it.anchor || '') === row.anchor) it.status = status;
      }
      try {
        const res = await fetch('/api/project/piano/item', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project: proj, kind: row.kind, anchor: row.anchor, status }),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
      } catch (e) {
        console.error('[piano] set status err', e);
        this.loadPiano();   // rollback dal server in caso di errore
      }
    },

    // ===== Statistiche workspace (F1c): metrics.db → KPI + grafici + insight =====
    openStatistiche() {
      this.view = 'statistiche';
      this.loadStats();
      this.loadSocialPerf();
      this.refreshIcons();
    },

    async loadSocialPerf() {
      const proj = this.currentProjectScopeName;
      if (!proj) { this.socialPerf = { posts: [], total: 0, collected: 0, updated_at: '' }; return; }
      try {
        const d = await this.fetchJson(`/api/project/social?project=${encodeURIComponent(proj)}`);
        this.socialPerf = d || { posts: [], total: 0, collected: 0, updated_at: '' };
      } catch (e) {
        this.socialPerf = { posts: [], total: 0, collected: 0, updated_at: '' };
      }
      this.refreshIcons();
    },

    async refreshSocialPerf() {
      const proj = this.currentProjectScopeName;
      if (!proj || this.socialPerfRefreshing) return;
      this.socialPerfRefreshing = true; this.socialPerfMsg = '';
      try {
        const res = await fetch('/api/project/social/refresh', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project: proj }),
        });
        if (!res.ok) { this.socialPerfMsg = '⚠️ ' + ((await res.json()).detail || 'Refresh failed'); return; }
        const d = await res.json();
        const errs = (d.errors || []).length;
        this.socialPerfMsg = `Engagement updated: ${d.collected} posts${errs ? ` · ${errs} not collected` : ''}.`;
        await this.loadSocialPerf();
        setTimeout(() => { this.socialPerfMsg = ''; }, 9000);
      } catch (e) {
        this.socialPerfMsg = '⚠️ Error: ' + (e.message || e);
      } finally {
        this.socialPerfRefreshing = false;
      }
    },

    _socialChannelLabel(ch) {
      return { instagram: 'Instagram', facebook: 'Facebook', linkedin: 'LinkedIn' }[ch] || ch;
    },

    async loadStats() {
      const proj = this.currentProjectScopeName;
      if (!proj) { this.statsData = null; return; }
      this.statsLoading = true;
      try {
        this.statsData = await this.fetchJson(`/api/project/metrics?project=${encodeURIComponent(proj)}&range_days=${this.statsRange}`);
      } catch (e) {
        console.error('[stats] load err', e);
        this.statsData = null;
      } finally {
        this.statsLoading = false;
        this.refreshIcons();
        this.$nextTick(() => this._renderStatCharts());
      }
    },

    setStatsRange(r) {
      if (this.statsRange === r) return;
      this.statsRange = r;
      this.loadStats();
    },

    async refreshMetrics() {
      const proj = this.currentProjectScopeName;
      if (!proj || this.statsRefreshing) return;
      this.statsRefreshing = true; this.statsMsg = '';
      try {
        const res = await fetch('/api/project/metrics/refresh', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project: proj }),
        });
        if (!res.ok) { this.statsMsg = '⚠️ ' + ((await res.json()).detail || 'Refresh failed'); return; }
        const d = await res.json();
        this.statsMsg = d.collected ? `Updated: ${d.collected} rows collected.` : ('ℹ️ ' + (d.note || 'No data collected.'));
        await this.loadStats();
        setTimeout(() => { this.statsMsg = ''; }, 8000);
      } catch (e) {
        this.statsMsg = '⚠️ Error: ' + (e.message || e);
      } finally {
        this.statsRefreshing = false;
      }
    },

    setStatsTab(t) {
      this.statsTab = t;
      this.$nextTick(() => { this.refreshIcons(); this._renderStatCharts(); });
    },

    _fmtNum(n) {
      if (n === null || n === undefined) return '—';
      return new Intl.NumberFormat('it-IT').format(n);
    },

    // costruisce il view-model di una KPI card (valore formattato + delta colorato)
    _kpi(key, label, fmt, sub, primary = false) {
      return this._kpiFrom(this.statsData?.kpis?.[key], key, label, fmt, sub, primary);
    },
    _kpiFrom(k, key, label, fmt, sub, primary = false) {
      if (!k) return { key, label, value: '—', sub: sub || '', primary, deltaLabel: '', deltaClass: 'neutral', deltaIcon: 'minus' };
      const v = k.value;
      let value = '—';
      if (v !== null && v !== undefined) {
        value = fmt === 'pct' ? v.toFixed(2) + '%' : (fmt === 'pos' ? v.toFixed(1) : (fmt === 'eur' ? '€' + this._fmtNum(v) : this._fmtNum(v)));
      }
      let deltaLabel = '', deltaClass = 'neutral', deltaIcon = 'minus';
      if (k.delta !== null && k.delta !== undefined && k.delta !== 0) {
        const good = k.better === 'down' ? k.delta < 0 : k.delta > 0;
        deltaClass = good ? 'up' : 'down';
        deltaIcon = k.delta > 0 ? 'arrow-up' : 'arrow-down';
        const mag = Math.abs(k.delta);
        deltaLabel = (fmt === 'pos' || fmt === 'pct') ? mag.toFixed(1) : this._fmtNum(mag);
      }
      return { key, label, value, sub: sub || '', primary, deltaLabel, deltaClass, deltaIcon };
    },

    get overviewKpis() {
      const p = `vs prev. ${this.statsRange}d`;
      return [
        this._kpi('clicks', 'Organic clicks', 'num', p, true),
        this._kpi('impressions', 'Impressions', 'num', p),
        this._kpi('ctr', 'CTR', 'pct', p),
        this._kpi('position', 'Average position', 'pos', p),
      ];
    },

    get analyticsKpis() {
      const p = `vs prev. ${this.statsRange}d`;
      return [
        this._kpi('sessions', 'Sessions', 'num', p, false),
        this._kpi('conversions', 'Conversions', 'num', p),
        this._kpi('clicks', 'SEO clicks', 'num', 'GSC organic'),
        this._kpi('ctr', 'Organic CTR', 'pct', p),
      ];
    },

    adsTermsView: 'wasted',
    get adsKpis() {
      const p = `vs prev. ${this.statsRange}d`;
      const a = this.statsData?.ads?.kpis || {};
      const native = this.statsData?.ads?.source === 'google';
      const cards = [
        this._kpiFrom(a.spend, 'aspend', 'Spend', 'eur', p, true),
        this._kpiFrom(a.conversions, 'aconv', 'Conversions', 'num', p),
        this._kpiFrom(a.revenue, 'arev', 'Conversion value', 'eur', p),
        this._kpiFrom(a.roas, 'aroas', 'ROAS', 'num', 'value / spend'),
      ];
      if (native) {
        cards.push(
          this._kpiFrom(a.cpa, 'acpa', 'CPA', 'eur', p),
          this._kpiFrom(a.ctr, 'actr', 'CTR', 'pct', p),
          this._kpiFrom(a.cpc, 'acpc', 'Avg. CPC', 'eur', p),
          this._kpiFrom(a.clicks, 'aclicks', 'Clicks', 'num', p),
        );
      }
      return cards;
    },

    get salesKpis() {
      const p = `vs prev. ${this.statsRange}d`;
      const k = this.statsData?.sales?.kpis || {};
      return [
        this._kpiFrom(k.revenue, 'srev', 'Revenue (orders)', 'eur', p, true),
        this._kpiFrom(k.orders, 'sord', 'Orders', 'num', p),
        this._kpiFrom(k.aov, 'saov', 'Avg. order value', 'eur', p),
        this._kpiFrom(k.new_customers, 'snew', 'New customers', 'num', p),
        this._kpiFrom(k.net_revenue, 'snet', 'Net revenue', 'eur', 'excl. tax & shipping'),
        this._kpiFrom(k.items, 'sitems', 'Items sold', 'num', p),
        this._kpiFrom({ value: this.statsData?.sales?.cash_roas ?? null, delta: null }, 'sroas', 'Cash ROAS', 'num', 'orders revenue / ads spend'),
        this._kpiFrom({ value: this.statsData?.sales?.b2b_share ?? null, delta: null }, 'sb2b', 'B2B share', 'pct', 'orders with company'),
      ];
    },

    get shoppingKpis() {
      const p = `vs prev. ${this.statsRange}d`;
      const m = this.statsData?.merchant?.kpis || {};
      return [
        this._kpiFrom(m.clicks, 'mclicks', 'Listing clicks', 'num', p, true),
        this._kpiFrom(m.impressions, 'mimpr', 'Impressions', 'num', p),
        this._kpiFrom(m.ctr, 'mctr', 'CTR', 'pct', p),
        this._kpiFrom(m.conversion_value, 'mval', 'Conversion value', 'eur', p),
      ];
    },

    get socialKpis() {
      const p = `vs prev. ${this.statsRange}d`;
      const s = this.statsData?.social || {};
      const k = s.kpis || {};
      return [
        this._kpiFrom(k.ig_reach, 'sreach', 'Reach IG', 'num', p, true),
        this._kpiFrom(k.fb_interactions, 'sint', 'FB interactions', 'num', p),
        this._kpiFrom({ value: s.followers?.instagram ?? null, delta: null }, 'sfig', 'Follower IG', 'num', 'snapshot'),
        this._kpiFrom({ value: s.followers?.facebook ?? null, delta: null }, 'sffb', 'Follower FB', 'num', 'snapshot'),
      ];
    },

    _renderStatCharts() {
      const C = window.Chart;
      if (!C || !this.statsData || !this.statsData.exists) return;
      const reg = (window._anjaStatCharts = window._anjaStatCharts || {});
      const BRAND = '#0b887b', INK = '#697386';
      const mk = (id, cfg) => {
        const el = document.getElementById(id);
        if (!el) return;                 // canvas non nel tab attivo
        if (reg[id]) { reg[id].destroy(); delete reg[id]; }
        reg[id] = new C(el, cfg);
      };
      const baseOpts = (extra = {}) => Object.assign({
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } },
      }, extra);

      if (this.statsTab === 'overview') {
        const g = this.statsData.gsc_series || [];
        mk('stat-chart-gsc', {
          type: 'line',
          data: { labels: g.map(p => p.date), datasets: [
            { label: 'Click', data: g.map(p => p.clicks), borderColor: BRAND, backgroundColor: 'rgba(11,136,123,.10)', yAxisID: 'y', tension: .3, fill: true, pointRadius: 0, borderWidth: 2 },
            { label: 'Impressions', data: g.map(p => p.impressions), borderColor: INK, borderDash: [5, 4], yAxisID: 'y1', tension: .3, pointRadius: 0, borderWidth: 1.5 },
          ] },
          options: baseOpts({ scales: {
            y: { position: 'left', beginAtZero: true, title: { display: true, text: 'Click' } },
            y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: 'Impressions' } },
            x: { ticks: { maxTicksLimit: 8, font: { size: 10 } } },
          } }),
        });
      }
      if (this.statsTab === 'analytics') {
        const a = this.statsData.ga_series || [];
        mk('stat-chart-ga', {
          type: 'line',
          data: { labels: a.map(p => p.date), datasets: [
            { label: 'Sessions', data: a.map(p => p.sessions), borderColor: BRAND, backgroundColor: 'rgba(11,136,123,.12)', tension: .3, fill: true, pointRadius: 0, borderWidth: 2 },
          ] },
          options: baseOpts({ plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true }, x: { ticks: { maxTicksLimit: 8, font: { size: 10 } } } } }),
        });
      }
      if (this.statsTab === 'social' && this.statsData.social) {
        const rows = this.statsData.social.series || [];
        const dates = [...new Set(rows.map(r => r.date))].sort();
        const by = ch => dates.map(d => (rows.find(r => r.date === d && r.channel === ch) || {}).reach ?? null);
        mk('stat-chart-social', {
          type: 'line',
          data: { labels: dates, datasets: [
            { label: 'Reach IG', data: by('instagram'), borderColor: BRAND, backgroundColor: 'rgba(11,136,123,.10)', tension: .3, fill: true, pointRadius: 0, borderWidth: 2, spanGaps: true },
            { label: 'Reach FB', data: by('facebook'), borderColor: INK, borderDash: [5, 4], tension: .3, pointRadius: 0, borderWidth: 1.5, spanGaps: true },
          ] },
          options: baseOpts({ scales: { y: { beginAtZero: true }, x: { ticks: { maxTicksLimit: 8, font: { size: 10 } } } } }),
        });
      }
      if (this.statsTab === 'shopping' && this.statsData.merchant) {
        const m = this.statsData.merchant.series || [];
        mk('stat-chart-merchant', {
          type: 'line',
          data: { labels: m.map(p => p.date), datasets: [
            { label: 'Click', data: m.map(p => p.clicks), borderColor: BRAND, backgroundColor: 'rgba(11,136,123,.10)', yAxisID: 'y', tension: .3, fill: true, pointRadius: 0, borderWidth: 2 },
            { label: 'Impressions', data: m.map(p => p.impressions), borderColor: INK, borderDash: [5, 4], yAxisID: 'y1', tension: .3, pointRadius: 0, borderWidth: 1.5 },
          ] },
          options: baseOpts({ scales: {
            y: { position: 'left', beginAtZero: true, title: { display: true, text: 'Click' } },
            y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: 'Impressions' } },
            x: { ticks: { maxTicksLimit: 8, font: { size: 10 } } },
          } }),
        });
      }
      if (this.statsTab === 'sales' && this.statsData.sales) {
        const s = this.statsData.sales.series || [];
        mk('stat-chart-sales', {
          type: 'bar',
          data: { labels: s.map(p => p.date), datasets: [
            { type: 'bar', label: 'Revenue €', data: s.map(p => p.revenue), backgroundColor: 'rgba(11,136,123,.5)', yAxisID: 'y' },
            { type: 'line', label: 'Orders', data: s.map(p => p.orders), borderColor: INK, tension: .3, pointRadius: 0, yAxisID: 'y1' },
          ] },
          options: baseOpts({ scales: { y: { beginAtZero: true, position: 'left' }, y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, ticks: { precision: 0 } }, x: { ticks: { maxTicksLimit: 8, font: { size: 10 } } } } }),
        });
      }
      if (this.statsTab === 'ads' && this.statsData.has_ads) {
        const s = this.statsData.ads_series || [];
        mk('stat-chart-ads', {
          type: 'bar',
          data: { labels: s.map(p => p.date), datasets: [
            { type: 'bar', label: 'Revenue €', data: s.map(p => p.revenue), backgroundColor: 'rgba(11,136,123,.5)', yAxisID: 'y' },
            { type: 'line', label: 'Spend €', data: s.map(p => p.spend), borderColor: INK, tension: .3, pointRadius: 0, yAxisID: 'y' },
          ] },
          options: baseOpts({ scales: { y: { beginAtZero: true }, x: { ticks: { maxTicksLimit: 8, font: { size: 10 } } } } }),
        });
      }
    },

    // ===== Connettori workspace (F1a): card-form sopra .secrets.env =====
    openConnettori() {
      this.view = 'connettori';
      this.loadConnectors();
      this.loadGoogleOauth();
      this.loadGoogleResources();
      this.refreshIcons();
    },

    async loadGoogleResources() {
      const proj = this.currentProjectScopeName;
      if (!proj) return;
      try {
        this.googleResources = await this.fetchJson('/api/google/resources?scope=' + encodeURIComponent(proj));
      } catch (e) {
        this.googleResources = null;   // Google non collegato → i campi restano input liberi
      }
    },

    connFieldOptions(key) {
      const r = this.googleResources;
      if (!r) return null;
      if (key === 'GSC_SITE') return (r.gsc_sites || []).map(s => ({ value: s, label: s }));
      if (key === 'GA4_PROPERTY_ID') return (r.ga4_properties || []).map(p => ({ value: p.id, label: p.name + ' (' + p.id + ')' }));
      if (key === 'MERCHANT_ACCOUNT_ID') return (r.merchant_accounts || []).map(a => ({ value: a.id, label: a.name + ' (' + a.id + ')' }));
      return null;
    },

    _resetChatInputHeight() {
      // l'auto-grow lascia l'height inline anche dopo l'invio: torna alla base
      document.querySelectorAll('textarea.chat__input').forEach(t => { t.style.height = ''; });
    },

    // --- F-DeepResearch: sezione Research -------------------------------------
    async copyText(text) {
      try { await navigator.clipboard.writeText(text); } catch (e) { /* http o permessi */ }
    },

    async loadResearchTasks() {
      try {
        const d = await this.fetchJson('/api/research/deep');
        this.research.tasks = d?.tasks || [];
        this.$nextTick(() => this.refreshIcons());
      } catch (e) { /* endpoint assente o errore transiente */ }
      // auto-poll finché c'è una task in corso e la view è aperta
      clearTimeout(this._researchPollTimer);
      if (this.view === 'research' && this.research.tasks.some(t => t.status === 'in_progress')) {
        this._researchPollTimer = setTimeout(() => this.loadResearchTasks(), 20000);
      }
    },

    async startResearch() {
      const query = this.research.query.trim();
      if (!query) return;
      this.research.launching = true;
      this.research.message = '';
      try {
        const res = await fetch('/api/research/deep', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query, mode: this.research.mode }),
        });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || 'launch failed');
        this.research.query = '';
        this.research.message = 'Launched — the report will arrive as a notification.';
        await this.loadResearchTasks();
      } catch (e) {
        this.research.message = 'Error: ' + (e.message || e);
      } finally {
        this.research.launching = false;
      }
    },

    async openResearchReport(t) {
      if (t.status !== 'completed') {
        this.research.current = null;
        return;
      }
      const d = await this.fetchJson(`/api/research/deep/${encodeURIComponent(t.task_id)}/report`);
      if (!d) {
        this.showToast('error', 'Report not available', '');
        return;
      }
      this.research.current = { task_id: t.task_id, path: d.path, content: d.content };
      this.$nextTick(() => this.refreshIcons());
    },

    async deleteResearch(t) {
      if (!confirm(`Delete the research and its report?\n"${(t.query || '').slice(0, 80)}"`)) return;
      try {
        const r = await fetch(`/api/research/deep/${encodeURIComponent(t.task_id)}`, { method: 'DELETE' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        if (this.research.current && this.research.current.task_id === t.task_id) this.research.current = null;
        await this.loadResearchTasks();
      } catch (e) {
        this.showToast('error', 'Delete failed', e.message || '');
      }
    },

    async uploadGoogleClient(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      this.gclientSetup.msg = ''; this.gclientSetup.ok = false;
      try {
        const text = await file.text();
        const res = await fetch('/api/google/oauth/client', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ client_json: text }),
        });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || 'upload failed');
        this.gclientSetup.ok = true;
        this.gclientSetup.msg = `✓ OAuth client saved (project ${d.project_id || '?'}, type ${d.kind}).`
          + (d.redirect_uri_ok ? ' Now click "Connect Google".' : ` ⚠ The client has no redirect URI ${d.redirect_uri_expected} — add it in Cloud Console before connecting.`);
        this.gclientSetup.open = false;
        await this.loadGoogleOauth();
      } catch (e) {
        this.gclientSetup.msg = 'Error: ' + (e.message || e);
      } finally {
        ev.target.value = '';
        this.$nextTick(() => this.refreshIcons());
      }
    },

    async loadGoogleOauth() {
      // scope: il workspace corrente, altrimenti hub (Settings → Integrations)
      const proj = this.currentProjectScopeName || 'hub';
      try {
        const d = await this.fetchJson('/api/google/oauth/status?scope=' + encodeURIComponent(proj));
        this.googleOauth = d || { connected: false, client_configured: false, token_scope: '', redirect_uri: '' };
      } catch (e) {
        this.googleOauth = { connected: false, client_configured: false, token_scope: '', redirect_uri: '' };
      }
    },

    connectGoogle() {
      const proj = this.currentProjectScopeName;
      if (!proj || !this.googleOauth.client_configured) return;
      window.location.href = '/api/google/oauth/start?scope=' + encodeURIComponent(proj);
    },

    // ===== Audit prodotti (Tier 2) =====
    openAudit() {
      this.view = 'audit';
      this.refreshIcons();
    },

    auditSummaryCards() {
      const s = this.audit.summary;
      if (!s) return [];
      return [
        { label: 'Products', value: s.count },
        { label: 'Avg SEO', value: s.avg_seo },
        { label: 'Avg E-E-A-T', value: s.avg_eeat },
        { label: 'Avg GEO', value: s.avg_geo },
        { label: 'Quick-win', value: s.quick_wins },
        { label: 'With GSC data', value: s.with_gsc },
      ];
    },

    auditScoreStyle(v) {
      const c = v >= 70 ? '#15803d' : v >= 40 ? '#b45309' : '#b91c1c';
      const bg = v >= 70 ? '#f0fdf4' : v >= 40 ? '#fffbeb' : '#fef2f2';
      return `display:inline-block;min-width:30px;text-align:center;padding:2px 6px;border-radius:6px;font-weight:600;font-size:12px;color:${c};background:${bg}`;
    },

    async runAudit(kind) {
      if (kind) this.audit.kind = kind;
      const proj = this.currentProjectScopeName;
      if (!proj || this.audit.loading) return;
      this.audit.loading = true; this.audit.msg = '';
      try {
        const res = await fetch('/api/project/audit', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project: proj, kind: this.audit.kind }),
        });
        const d = await res.json().catch(() => ({}));
        if (!res.ok) {
          this.audit.msg = '⚠️ ' + (d.detail || `Error ${res.status}`);
          this.audit.products = []; this.audit.summary = null;
          return;
        }
        this.audit.products = d.products || [];
        this.audit.summary = d.summary || null;
        this.refreshIcons();
      } catch (e) {
        this.audit.msg = '⚠️ ' + (e.message || e);
      } finally {
        this.audit.loading = false;
      }
    },

    // Manda un contenuto al seo-copy: apre la chat del workspace con un prompt
    // pre-compilato (l'utente lo vede e invia). L'auto-route su seo/eeat smista.
    sendToSeoCopy(p) {
      const s = p.scores;
      const prompt = `Fix the SEO / E-E-A-T / GEO of «${p.name}» (${p.permalink}).\n`
        + `Current scores — SEO ${s.seo}, E-E-A-T ${s.eeat}, GEO ${s.geo}.\n`
        + `Analyze the content and propose the priority fixes as a draft (read-back after each change).`;
      this.view = 'chat';
      this.currentConversation = null;
      this.currentConvId = null;
      this.messages = [];
      this.$nextTick(() => {
        this.chatInput = prompt;
        if (!this.wsConnected) this.connectWs();
        this.refreshIcons();
        const el = document.querySelector('textarea[x-model="chatInput"], input[x-model="chatInput"]');
        if (el) el.focus();
      });
    },

    // ===== Integrazioni hub (connettori condivisi, tab Settings) + generazione immagini =====
    _initHubConnDraft() {
      const draft = {};
      for (const con of (this.hubConnectors || []))
        for (const f of con.fields) draft[f.key] = f.secret ? '' : (f.value || '');
      this.hubConnDraft = draft;
    },

    async loadHubConnectors() {
      this.hubConnectors = null; this.hubConnMsg = '';
      try {
        const data = await this.fetchJson('/api/hub/connectors');
        this.hubConnectors = data?.connectors || [];
        this._initHubConnDraft();
      } catch (e) {
        console.error('[hub-connectors] load err', e);
        this.hubConnectors = [];
      } finally {
        this.refreshIcons();
      }
    },

    async saveHubConnectors() {
      if (this.hubConnSaving) return;
      this.hubConnSaving = true; this.hubConnMsg = '';
      try {
        const res = await fetch('/api/hub/connectors', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ values: this.hubConnDraft }),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        const data = await res.json();
        this.hubConnectors = data.connectors || this.hubConnectors;
        this._initHubConnDraft();
        this.hubConnMsg = '✓ Saved to the hub\'s encrypted vault.';
        this.refreshIcons();
        setTimeout(() => { this.hubConnMsg = ''; }, 4000);
      } catch (e) {
        console.error('[hub-connectors] save err', e);
        this.hubConnMsg = '⚠️ Error: ' + (e.message || e);
      } finally {
        this.hubConnSaving = false;
      }
    },

    async loadMediaModels(scope) {
      try {
        const data = await this.fetchJson('/api/media/models?scope=' + encodeURIComponent(scope || 'hub'));
        this.mediaGen.models = data?.models || [];
        const ready = this.mediaGen.models.find(m => m.ready);
        this.mediaGen.model = (ready || this.mediaGen.models[0] || {}).id || '';
      } catch (e) {
        this.mediaGen.models = [];
      }
    },

    async generateMedia(scope) {
      if (this.mediaGen.busy || !this.mediaGen.prompt.trim() || !this.mediaGen.model) return;
      this.mediaGen.busy = true; this.mediaGen.msg = '';
      try {
        const res = await fetch('/api/media/generate', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope: scope || 'hub', prompt: this.mediaGen.prompt.trim(), model: this.mediaGen.model }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { this.mediaGen.msg = '⚠️ ' + (data.detail || `Error ${res.status}`); return; }
        this.mediaGen.msg = `✓ Generated (${data.model}) · ${(data.files || []).length} file(s)`;
        this.mediaGen.prompt = '';
        if (scope === 'hub') await this.loadMedia(); else await this.loadProjectMedia();
        this.refreshIcons();
        setTimeout(() => { this.mediaGen.msg = ''; }, 8000);
      } catch (e) {
        this.mediaGen.msg = '⚠️ Error: ' + (e.message || e);
      } finally {
        this.mediaGen.busy = false;
      }
    },

    _initConnDraft() {
      // plain → valore corrente; secret → '' (vuoto = invariato lato server)
      const draft = {};
      for (const con of (this.connectors || []))
        for (const f of con.fields) draft[f.key] = f.secret ? '' : (f.value || '');
      this.connDraft = draft;
    },

    async loadConnectors() {
      const proj = this.currentProjectScopeName;
      if (!proj) { this.connectors = null; return; }
      this.connLoading = true; this.connMsg = '';
      try {
        const data = await this.fetchJson(`/api/project/connectors?project=${encodeURIComponent(proj)}`);
        this.connectors = data?.connectors || [];
        this.connMaterialized = !!data?.materialized;
        this._initConnDraft();
      } catch (e) {
        console.error('[connectors] load err', e);
        this.connectors = null;
      } finally {
        this.connLoading = false;
        this.refreshIcons();
      }
    },

    async saveConnectors() {
      const proj = this.currentProjectScopeName;
      if (!proj || this.connSaving) return;
      this.connSaving = true; this.connMsg = '';
      try {
        const res = await fetch('/api/project/connectors', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project: proj, values: this.connDraft }),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        const data = await res.json();
        this.connectors = data.connectors || this.connectors;
        this.connMaterialized = !!data.materialized;
        this._initConnDraft();   // ripulisce gli input secret + aggiorna stato/plain
        this.connMsg = 'Connectors saved to the encrypted vault.';
        this.refreshIcons();
        setTimeout(() => { this.connMsg = ''; }, 4000);
      } catch (e) {
        console.error('[connectors] save err', e);
        alert('Error saving connectors: ' + (e.message || e));
      } finally {
        this.connSaving = false;
      }
    },

    async toggleMaterialize() {
      const proj = this.currentProjectScopeName;
      if (!proj || this.connSaving) return;
      this.connSaving = true; this.connMsg = '';
      const turnOn = !this.connMaterialized;
      try {
        const res = await fetch('/api/project/connectors/materialize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project: proj, on: turnOn }),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        const data = await res.json();
        this.connMaterialized = !!data.materialized;
        this.connMsg = turnOn
          ? 'Secrets materialized in plaintext for the runtime.'
          : 'Secrets removed from the runtime (they remain in the encrypted vault).';
        this.refreshIcons();
        setTimeout(() => { this.connMsg = ''; }, 4000);
      } catch (e) {
        console.error('[connectors] materialize err', e);
        alert('Error: ' + (e.message || e));
      } finally {
        this.connSaving = false;
      }
    },

    connStatusLabel(con) {
      // gruppi tutti-opzionali (es. key immagini): conta, non "connected" con 1 su 6
      if (con.all_optional) return `${con.set_count} of ${con.total} configured`;
      if (con.status === 'connected') return 'connected';
      if (con.status === 'partial') return `partial ${con.set_count}/${con.total}`;
      return 'missing';
    },

    connStatusClass(status) {
      return status === 'connected' ? 'success' : (status === 'partial' ? 'warn' : 'neutral');
    },

    // ===== Auth / Identità (F4 Concierge) =====
    async loadAuth() {
      try {
        const d = await this.fetchJson('/api/auth/me');
        if (d) this.auth = { mode: d.mode, authenticated: d.authenticated, user: d.user, has_users: d.has_users, ready: true };
        else this.auth.ready = true;
      } catch (e) { this.auth.ready = true; }
      this.refreshIcons();
    },

    // Ruolo effettivo per gating UI (le azioni admin-only sono comunque enforced lato
    // server). True in personal mode (gate NO-OP) o se owner/admin in concierge.
    isAdmin() {
      return this.auth.mode !== 'concierge' || ['owner', 'admin'].includes(this.auth.user?.role);
    },

    async doLogin() {
      if (this.loginForm.busy) return;
      this.loginForm.busy = true; this.loginForm.error = '';
      try {
        const res = await fetch('/api/auth/login', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slug: (this.loginForm.slug || '').trim().toLowerCase(), password: this.loginForm.password }),
        });
        if (res.status === 401) { this.loginForm.error = 'Invalid credentials'; return; }
        if (!res.ok) throw new Error(await res.text());
        window.location.reload();   // ricarica con la sessione attiva
      } catch (e) {
        this.loginForm.error = 'Error: ' + (e.message || e);
      } finally {
        this.loginForm.busy = false;
      }
    },

    async doLogout() {
      try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
      window.location.reload();
    },

    openIdentita() {
      this.view = 'identita';
      this.identitaMsg = '';
      this.loadUsers();
      if (this.auth.mode === 'concierge') this.loadWsAccess();
      this.refreshIcons();
    },

    // Gestione membri per-workspace (F4b): owner/admin accedono a tutto, i member
    // solo ai ws dove sono in members[].
    async loadWsAccess() {
      try {
        const d = await this.fetchJson('/api/workspaces');
        this.wsAccessList = (d?.workspaces || []).filter(w => w.kind === 'internal');
      } catch (e) { this.wsAccessList = []; }
      const out = {};
      for (const w of this.wsAccessList) {
        try {
          const m = await this.fetchJson(`/api/workspaces/${encodeURIComponent(w.name)}/members`);
          out[w.name] = m?.members || [];
        } catch (e) { out[w.name] = []; }
      }
      this.wsMembers = out;
      this.refreshIcons();
    },

    isWsMember(ws, slug) { return (this.wsMembers[ws] || []).includes(slug); },

    async toggleWsMember(ws, slug) {
      const cur = new Set(this.wsMembers[ws] || []);
      cur.has(slug) ? cur.delete(slug) : cur.add(slug);
      const arr = [...cur];
      try {
        const res = await fetch(`/api/workspaces/${encodeURIComponent(ws)}/members`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ members: arr }),
        });
        if (!res.ok) { this.showToast('error', '⚠️ ' + ((await res.json()).detail || 'Error')); return; }
        const d = await res.json();
        this.wsMembers = { ...this.wsMembers, [ws]: d.members || arr };
      } catch (e) {
        this.showToast('error', 'Error: ' + (e.message || e));
      }
    },

    // ===== Catalogo contenuti del sito (workspace marketing) =====
    openCatalogo() {
      this.view = 'catalogo';
      this.catalogoQuery = '';
      this.loadCatalogo();
      this.refreshIcons();
    },

    async loadCatalogo() {
      const proj = this.currentProjectScopeName;
      if (!proj) { this.siteCatalog = { kinds: {}, generated: '', exists: false }; return; }
      this.catalogoLoading = true;
      try {
        const d = await this.fetchJson(`/api/project/catalogo?project=${encodeURIComponent(proj)}`);
        this.siteCatalog = d || { kinds: {}, generated: '', exists: false };
        this.catalogoTab = this.catalogoKinds[0] || '';
      } catch (e) {
        console.error('[catalogo] load err', e);
        this.siteCatalog = { kinds: {}, generated: '', exists: false };
      } finally {
        this.catalogoLoading = false;
        this.refreshIcons();
      }
    },

    get catalogoKinds() {
      return Object.keys(this.siteCatalog.kinds || {}).filter(k => (this.siteCatalog.kinds[k] || []).length);
    },

    get catalogoRows() {
      const items = (this.siteCatalog.kinds || {})[this.catalogoTab] || [];
      const q = (this.catalogoQuery || '').trim().toLowerCase();
      if (!q) return items;
      return items.filter(r => (r.title || '').toLowerCase().includes(q) || (r.slug || '').toLowerCase().includes(q));
    },

    async syncCatalogo() {
      const proj = this.currentProjectScopeName;
      if (!proj || this.catalogoSyncing) return;
      this.catalogoSyncing = true; this.catalogoMsg = '';
      try {
        const res = await fetch('/api/project/catalogo/sync', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project: proj }),
        });
        if (!res.ok) { this.catalogoMsg = '⚠️ ' + ((await res.json()).detail || 'Sync failed'); return; }
        const d = await res.json();
        const c = d.counts || {};
        this.catalogoMsg = `Synced: ${Object.entries(c).map(([k, v]) => `${v ?? '—'} ${k}`).join(' · ')}`;
        await this.loadCatalogo();
        setTimeout(() => { this.catalogoMsg = ''; }, 6000);
      } catch (e) {
        this.catalogoMsg = '⚠️ Error: ' + (e.message || e);
      } finally {
        this.catalogoSyncing = false;
      }
    },

    // ===== Marketplace workspace attivabili (F5) =====
    openMarketplace() {
      this.view = 'marketplace';
      this.catalogActivating = null;
      this.loadCatalog();
      this.refreshIcons();
    },

    async loadCatalog() {
      this.catalogLoading = true;
      try {
        const d = await this.fetchJson('/api/blueprints');
        this.catalogBlueprints = d?.blueprints || [];
      } catch (e) {
        console.error('[catalog] load err', e);
        this.catalogBlueprints = [];
      } finally {
        this.catalogLoading = false;
        this.refreshIcons();
      }
    },

    startActivate(bp) {
      this.catalogActivating = bp.name;
      this.catalogForm = { brand: '', backend: bp.default_backend || (bp.backends[0] || ''), ecommerce: false, busy: false, error: '' };
      this.refreshIcons();
    },

    async doActivate(bp) {
      if (this.catalogForm.busy) return;
      const brand = (this.catalogForm.brand || '').trim();
      if (!brand) { this.catalogForm.error = 'Enter the brand name'; return; }
      this.catalogForm.busy = true; this.catalogForm.error = '';
      try {
        const res = await fetch('/api/workspaces/from-blueprint', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            brand_name: brand, blueprint: bp.name,
            backend: this.catalogForm.backend, ecommerce: this.catalogForm.ecommerce,
          }),
        });
        if (!res.ok) { this.catalogForm.error = (await res.json()).detail || 'Create failed'; return; }
        const data = await res.json();
        this.catalogActivating = null;
        await this.loadRegistry();
        this.showToast('success', '✅ Workspace activated', data.slug || brand);
        if (data.slug) this.switchToProject(data.slug);
      } catch (e) {
        this.catalogForm.error = 'Error: ' + (e.message || e);
      } finally {
        this.catalogForm.busy = false;
      }
    },

    async loadUsers() {
      try {
        const d = await this.fetchJson('/api/auth/users');
        this.identitaUsers = d?.users || [];
      } catch (e) { this.identitaUsers = []; }
      this.refreshIcons();
    },

    async setMode(mode) {
      if (this.auth.mode === mode) return;
      this.identitaMsg = '';
      try {
        const res = await fetch('/api/auth/mode', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode }),
        });
        if (res.status === 402) {
          this.identitaMsg = '🔒 Concierge mode is the Business edition. Activate a license key below to unlock it.';
          return;
        }
        if (!res.ok) {
          const j = await res.json().catch(() => ({}));
          this.identitaMsg = (typeof j.detail === 'string' ? j.detail : 'Mode switch failed');
          return;
        }
        const d = await res.json();
        this.auth.mode = d.mode;
        // passare a concierge → ricarica per attivare il gate/login
        if (d.mode === 'concierge') window.location.reload();
      } catch (e) {
        this.identitaMsg = 'Error: ' + (e.message || e);
      }
    },

    async createUser() {
      if (this.identitaBusy) return;
      this.identitaBusy = true; this.newUser.error = '';
      try {
        const res = await fetch('/api/auth/users', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            slug: (this.newUser.slug || '').trim().toLowerCase(), name: this.newUser.name,
            password: this.newUser.password, role: this.identitaUsers.length ? this.newUser.role : undefined,
          }),
        });
        if (!res.ok) { this.newUser.error = (await res.json()).detail || 'Error'; return; }
        this.newUser = { slug: '', name: '', password: '', role: 'member', error: '' };
        this.loadUsers();
      } catch (e) {
        this.newUser.error = 'Error: ' + (e.message || e);
      } finally {
        this.identitaBusy = false;
      }
    },

    async changeRole(u, role) {
      if (u.role === role) return;
      try {
        const res = await fetch(`/api/auth/users/${encodeURIComponent(u.slug)}/role`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role }),
        });
        if (!res.ok) { this.showToast('error', '⚠️ ' + ((await res.json()).detail || 'Role error')); }
      } catch (e) {
        this.showToast('error', 'Error: ' + (e.message || e));
      } finally {
        this.loadUsers();   // riallinea la select allo stato reale (anche su rifiuto)
      }
    },

    async resetPassword(u) {
      const pw = prompt(`New password for @${u.slug} (min 8 characters):`);
      if (pw === null) return;
      try {
        const res = await fetch(`/api/auth/users/${encodeURIComponent(u.slug)}/password`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: pw }),
        });
        if (!res.ok) { this.showToast('error', '⚠️ ' + ((await res.json()).detail || 'Error')); return; }
        this.showToast('success', '✅ Password updated', '@' + u.slug);
      } catch (e) {
        this.showToast('error', 'Error: ' + (e.message || e));
      }
    },

    async deleteUser(u) {
      if (!confirm(`Delete user @${u.slug}? This action is irreversible.`)) return;
      try {
        const res = await fetch(`/api/auth/users/${encodeURIComponent(u.slug)}`, { method: 'DELETE' });
        if (!res.ok) { this.showToast('error', '⚠️ ' + ((await res.json()).detail || 'Error')); return; }
        this.showToast('success', '🗑 User deleted', '@' + u.slug);
        this.loadUsers();
      } catch (e) {
        this.showToast('error', 'Error: ' + (e.message || e));
      }
    },

    // ===== Brain personale/condiviso (F3): note .md libere =====
    openBrain() {
      this.view = 'brain';
      this.brainOpen = null;
      this.brainEditMode = false;
      this.brainGraphOpen = false;
      this.loadBrainNotes();
      this.refreshIcons();
    },

    setBrainScope(scope) {
      if (this.brainScope === scope) return;
      this.brainScope = scope;
      this.brainOpen = null;
      this.brainEditMode = false;
      this.brainGraphOpen = false;
      this.brainQuery = '';
      this.loadBrainNotes();
    },

    _brainParams() {
      const p = new URLSearchParams({ scope: this.brainScope });
      if (this.brainScope === 'user' && this.brainUser) p.set('user', this.brainUser);
      return p;
    },

    async loadBrainNotes() {
      const p = this._brainParams();
      if (this.brainQuery.trim()) p.set('q', this.brainQuery.trim());
      try {
        const data = await this.fetchJson(`/api/brain/notes?${p}`);
        this.brainNotes = data?.notes || [];
        if (data?.user) this.brainUser = data.user;
      } catch (e) {
        console.error('[brain] load err', e);
        this.brainNotes = [];
      }
      this.refreshIcons();
    },

    async openBrainNote(slug) {
      const p = this._brainParams();
      p.set('slug', slug);
      try {
        const res = await fetch(`/api/brain/note?${p}`);
        if (res.status === 404) {
          // link a una nota inesistente → bozza nuova con quel titolo
          this.newBrainNote(slug.replace(/-/g, ' '));
          return;
        }
        if (!res.ok) throw new Error(`${res.status}`);
        this.brainOpen = await res.json();
        this.brainEditMode = false;
        this.refreshIcons();
      } catch (e) {
        console.error('[brain] open err', e);
      }
    },

    newBrainNote(title = '') {
      this.brainOpen = { slug: '', title: title, body: '', links: [], backlinks: [] };
      this.brainDraftTitle = title;
      this.brainDraftBody = '';
      this.brainEditMode = true;
      this.refreshIcons();
    },

    editBrainNote() {
      this.brainDraftTitle = this.brainOpen.title || '';
      this.brainDraftBody = this.brainOpen.body || '';
      this.brainEditMode = true;
      this.refreshIcons();
    },

    cancelBrainEdit() {
      this.brainEditMode = false;
      if (!this.brainOpen.slug) this.brainOpen = null;   // bozza mai salvata
      this.refreshIcons();
    },

    async saveBrainNote() {
      if (this.brainSaving || !this.brainDraftTitle.trim()) return;
      this.brainSaving = true;
      try {
        const res = await fetch('/api/brain/note', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            scope: this.brainScope, user: this.brainUser,
            slug: this.brainOpen.slug || '',
            title: this.brainDraftTitle, body: this.brainDraftBody,
          }),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        const saved = await res.json();
        this.brainEditMode = false;
        await this.loadBrainNotes();
        await this.openBrainNote(saved.slug);
      } catch (e) {
        console.error('[brain] save err', e);
        alert('Error saving note: ' + (e.message || e));
      } finally {
        this.brainSaving = false;
      }
    },

    async deleteBrainNote() {
      if (!this.brainOpen?.slug || !confirm(`Delete the note "${this.brainOpen.title}"?`)) return;
      try {
        await fetch('/api/brain/note/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope: this.brainScope, user: this.brainUser, slug: this.brainOpen.slug }),
        });
        this.brainOpen = null;
        this.loadBrainNotes();
      } catch (e) {
        console.error('[brain] delete err', e);
      }
    },

    async promoteBrainNote() {
      if (!this.brainOpen?.slug) return;
      try {
        const res = await fetch('/api/brain/promote', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user: this.brainUser, slug: this.brainOpen.slug }),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        this.showToast('success', '⬆️ Promoted to the shared brain', this.brainOpen.title);
      } catch (e) {
        console.error('[brain] promote err', e);
        alert('Error promoting: ' + (e.message || e));
      }
    },

    _slugify(s) {
      return (s || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80);
    },

    brainPreview() {
      const md = this.brainOpen?.body || '';
      const esc = (s) => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      // senza sanitizer non iniettiamo HTML: testo escapato (il brain condiviso è
      // multi-utente → markdown→HTML grezzo sarebbe stored-XSS).
      if (!window.DOMPurify || !window.marked) return esc(md).replace(/\n/g, '<br>');
      let html;
      try {
        html = window.DOMPurify.sanitize(window.marked.parse(md));
      } catch (e) {
        return esc(md).replace(/\n/g, '<br>');
      }
      // [[link]] / [[link|alias]] → anchor cliccabile (delega su .brain-wikilink).
      // Eseguito su HTML GIÀ sanitizzato: target/alias sono testo escapato, lo slug
      // è ridotto a [a-z0-9-] → nessun reintro di XSS.
      return html.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (m, target, alias) =>
        `<a class="brain-wikilink" data-slug="${this._slugify(target)}">${(alias || target).trim()}</a>`);
    },

    brainRenderClick(e) {
      const a = e.target.closest('.brain-wikilink');
      if (a && a.dataset.slug) { e.preventDefault(); this.openBrainNote(a.dataset.slug); }
    },

    // ---- Graph viz (force-directed): note=nodi, [[link]]=archi ----
    openBrainGraph() {
      this.computeBrainGraph();
      this.brainGraphOpen = true;
      this.refreshIcons();
    },

    computeBrainGraph() {
      const notes = this.brainNotes || [];
      const N = notes.length, W = 760, H = 460;
      const idx = {}; notes.forEach((n, i) => { idx[n.slug] = i; });
      const nodes = notes.map((n, i) => {
        const a = (2 * Math.PI * i) / Math.max(N, 1);
        return { slug: n.slug, title: n.title, x: W / 2 + Math.cos(a) * 150, y: H / 2 + Math.sin(a) * 120, vx: 0, vy: 0, deg: 0 };
      });
      const seen = new Set(), edges = [];
      notes.forEach(n => (n.links || []).forEach(l => {
        const a = idx[n.slug], b = idx[l];
        if (b === undefined || a === b) return;
        const k = a < b ? `${a}-${b}` : `${b}-${a}`;
        if (seen.has(k)) return;
        seen.add(k); edges.push([a, b]); nodes[a].deg++; nodes[b].deg++;
      }));
      for (let it = 0; it < 170; it++) {
        for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
          let dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y, d2 = dx * dx + dy * dy + 0.01;
          const d = Math.sqrt(d2), f = 2400 / d2, fx = f * dx / d, fy = f * dy / d;
          nodes[i].vx += fx; nodes[i].vy += fy; nodes[j].vx -= fx; nodes[j].vy -= fy;
        }
        for (const [a, b] of edges) {
          let dx = nodes[b].x - nodes[a].x, dy = nodes[b].y - nodes[a].y;
          const d = Math.sqrt(dx * dx + dy * dy) + 0.01, f = (d - 90) * 0.02, fx = f * dx / d, fy = f * dy / d;
          nodes[a].vx += fx; nodes[a].vy += fy; nodes[b].vx -= fx; nodes[b].vy -= fy;
        }
        for (const nd of nodes) {
          nd.vx += (W / 2 - nd.x) * 0.005; nd.vy += (H / 2 - nd.y) * 0.005;
          nd.vx *= 0.85; nd.vy *= 0.85;
          nd.x += Math.max(-20, Math.min(20, nd.vx)); nd.y += Math.max(-20, Math.min(20, nd.vy));
          nd.x = Math.max(24, Math.min(W - 24, nd.x)); nd.y = Math.max(28, Math.min(H - 16, nd.y));
        }
      }
      this.brainGraph = {
        w: W, h: H,
        nodes: nodes.map(n => ({ slug: n.slug, title: n.title, x: n.x, y: n.y, r: 6 + Math.min(n.deg * 2, 10) })),
        edges: edges.map(([a, b]) => ({ x1: nodes[a].x, y1: nodes[a].y, x2: nodes[b].x, y2: nodes[b].y })),
      };
    },

    // SVG come stringa (Alpine x-for non funziona dentro <svg>) + click delegato.
    brainGraphSvg() {
      const g = this.brainGraph;
      if (!g.nodes.length) return '';
      const esc = (s) => (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      const r1 = (v) => v.toFixed(1);
      let s = `<svg viewBox="0 0 ${g.w} ${g.h}" preserveAspectRatio="xMidYMid meet" style="width:100%;height:auto;max-height:560px">`;
      for (const e of g.edges) s += `<line x1="${r1(e.x1)}" y1="${r1(e.y1)}" x2="${r1(e.x2)}" y2="${r1(e.y2)}" class="bg-edge"></line>`;
      for (const n of g.nodes) {
        const label = esc(n.title.length > 22 ? n.title.slice(0, 21) + '…' : n.title);
        s += `<g class="bg-node" data-slug="${esc(n.slug)}"><circle cx="${r1(n.x)}" cy="${r1(n.y)}" r="${n.r}"></circle>`
          + `<text x="${r1(n.x)}" y="${r1(n.y - n.r - 5)}" text-anchor="middle">${label}</text></g>`;
      }
      return s + '</svg>';
    },

    brainGraphClick(e) {
      const g = e.target.closest('.bg-node');
      if (g && g.dataset.slug) this.openBrainNodeFromGraph(g.dataset.slug);
    },

    openBrainNodeFromGraph(slug) {
      this.brainGraphOpen = false;
      this.openBrainNote(slug);
    },

    async loadKanban() {
      const params = new URLSearchParams();
      // Filter by scope: in workspace scope → solo task di quel workspace.
      // In hub scope → solo task scope=hub (i workspace hanno i loro kanban dedicati).
      // Nota: frontend usa `project:<name>` per il routing UI, backend usa `workspace:<name>`.
      if (this.isProjectScope) {
        const backendScope = this.workspaceScope.replace(/^project:/, 'workspace:');
        params.set('scope', backendScope);
      } else {
        params.set('scope', 'hub');
      }
      params.set('include_archived', 'false');
      try {
        const data = await this.fetchJson(`/api/kanban/tasks?${params}`);
        this.kanban.tasks = data.tasks || [];
        this.kanban.stats = data.stats || {};
        this.refreshIcons();
      } catch (e) {
        console.error('[kanban] load err', e);
      }
    },

    _kanbanConnectWs() {
      if (this.kanban.ws && this.kanban.ws.readyState <= 1) return;
      try {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${proto}//${window.location.host}/ws/kanban`);
        this.kanban.ws = ws;
        ws.onmessage = (ev) => {
          try {
            const event = JSON.parse(ev.data);
            // Refresh on any kanban event
            this.loadKanban();
            // Optional: toast notifications per eventi specifici
            if (event.event === 'auto_promoted' && (event.task_ids || []).length > 0) {
              this.showToast('info', '↗ Auto-promoted', `${event.task_ids.length} task(s)`);
            }
          } catch (e) {}
        };
        ws.onclose = () => { this.kanban.ws = null; };
      } catch (e) {
        console.error('[kanban] ws connect err', e);
      }
    },

    openKanbanWizard() {
      this.kanban.wizardOpen = true;
      this.kanban.wizardError = '';
      this.kanban.newTask = {
        title: '', body: '',
        scope: this.isProjectScope ? this.workspaceScope.replace(/^project:/, 'workspace:') : 'hub',
        assignee: this.isProjectScope ? (this.projects.find(p => p.name === this.currentProjectScopeName)?.responsabile || '') : '',
        priority: 1,
      };
      this.$nextTick(() => this.refreshIcons());
    },

    async submitKanbanTask() {
      const t = this.kanban.newTask;
      if (!t.title.trim() || this.kanban.wizardBusy) return;
      this.kanban.wizardBusy = true;
      this.kanban.wizardError = '';
      try {
        const res = await fetch('/api/kanban/tasks', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: t.title.trim(),
            body: t.body || '',
            scope: t.scope || 'hub',
            assignee: t.assignee || '',
            priority: parseInt(t.priority || 1),
          }),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        this.kanban.wizardOpen = false;
        this.showToast('success', '➕ Task created', t.title);
        // WS broadcast triggererà loadKanban automaticamente
      } catch (e) {
        this.kanban.wizardError = e.message || String(e);
      } finally {
        this.kanban.wizardBusy = false;
      }
    },

    async openKanbanTask(taskId) {
      try {
        const data = await this.fetchJson(`/api/kanban/tasks/${taskId}`);
        this.kanban.detailTask = data;
        this.kanban.detailOpen = true;
        this.kanban.commentInput = '';
        this.refreshIcons();
      } catch (e) {
        this.showToast('error', 'Load failed', e.message || String(e));
      }
    },

    async changeKanbanStatus(taskId, status) {
      const payload = { status };
      if (status === 'blocked') {
        const reason = prompt('Reason for blocking?');
        if (!reason) return;
        payload.block_reason = reason;
      }
      try {
        const res = await fetch(`/api/kanban/tasks/${taskId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        const updated = await res.json();
        this.kanban.detailTask = updated;
      } catch (e) {
        this.showToast('error', 'Update failed', e.message || String(e));
      }
    },

    async addKanbanComment() {
      const content = (this.kanban.commentInput || '').trim();
      if (!content || !this.kanban.detailTask) return;
      try {
        await fetch(`/api/kanban/tasks/${this.kanban.detailTask.id}/comment`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content, author: 'human' }),
        });
        this.kanban.commentInput = '';
        // Reload task detail
        await this.openKanbanTask(this.kanban.detailTask.id);
      } catch (e) {
        this.showToast('error', 'Comment failed', e.message || String(e));
      }
    },

    async deleteKanbanTask(taskId) {
      if (!confirm('Delete this task?')) return;
      try {
        const res = await fetch(`/api/kanban/tasks/${taskId}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        this.kanban.detailOpen = false;
        this.showToast('success', '🗑 Deleted', `task #${taskId}`);
      } catch (e) {
        this.showToast('error', 'Delete failed', e.message || String(e));
      }
    },

    onKanbanDragStart(ev, task) {
      this.kanban._draggedTask = task;
      try { ev.dataTransfer.effectAllowed = 'move'; } catch (e) {}
    },

    async onKanbanDrop(ev, newStatus) {
      const task = this.kanban._draggedTask;
      this.kanban._draggedTask = null;
      if (!task || task.status === newStatus) return;
      const payload = { status: newStatus };
      if (newStatus === 'blocked') {
        const reason = prompt('Reason for blocking?');
        if (!reason) return;
        payload.block_reason = reason;
      }
      try {
        await fetch(`/api/kanban/tasks/${task.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } catch (e) {
        this.showToast('error', 'Move failed', e.message || String(e));
      }
    },

    // ===== Fase 22.9+ — File upload =====
    _detectSubdir() {
      // cwd corrente del browser (hub o project) → determina subdir di destinazione
      // Default 'files' se siamo a root o in subdir non whitelisted
      const cwd = (this.view === 'hub-files' ? this.hubFiles.cwd : this.projectFiles.cwd) || '';
      const first = cwd.split('/')[0] || '';
      return ['files', 'data', 'scripts'].includes(first) ? first : 'files';
    },

    async uploadHubFile(event) {
      const files = Array.from(event.target.files || []);
      if (!files.length) return;
      const subdir = this._detectSubdir();
      this.hubUploadBusy = true;
      try {
        for (const f of files) {
          const fd = new FormData();
          fd.append('file', f);
          fd.append('subdir', subdir);
          fd.append('overwrite', 'false');
          const res = await fetch('/api/hub/upload', { method: 'POST', body: fd });
          if (!res.ok) {
            const txt = await res.text();
            // Su 409 (already exists), chiedi conferma overwrite
            if (res.status === 409 && confirm(`${f.name} already exists. Overwrite?`)) {
              fd.set('overwrite', 'true');
              const r2 = await fetch('/api/hub/upload', { method: 'POST', body: fd });
              if (!r2.ok) throw new Error(`${r2.status}: ${await r2.text()}`);
            } else {
              throw new Error(`${res.status}: ${txt}`);
            }
          }
          const data = await res.json().catch(() => ({}));
          this.showToast('success', '📤 Uploaded', `${data.path} (${this.formatFileSize(data.size || 0)})`);
        }
        await this.loadHubFilesDir(this.hubFiles.cwd);
        this.loadHubRecentFiles();
      } catch (e) {
        this.showToast('error', 'Upload failed', e.message || String(e));
      } finally {
        this.hubUploadBusy = false;
        event.target.value = '';  // reset input
      }
    },

    async uploadProjectFile(event) {
      const files = Array.from(event.target.files || []);
      if (!files.length || !this.projectFiles.project) return;
      const subdir = this._detectSubdir();
      this.projectUploadBusy = true;
      try {
        for (const f of files) {
          const fd = new FormData();
          fd.append('project', this.projectFiles.project);
          fd.append('file', f);
          fd.append('subdir', subdir);
          fd.append('overwrite', 'false');
          const res = await fetch('/api/project/upload', { method: 'POST', body: fd });
          if (!res.ok) {
            if (res.status === 409 && confirm(`${f.name} already exists. Overwrite?`)) {
              fd.set('overwrite', 'true');
              const r2 = await fetch('/api/project/upload', { method: 'POST', body: fd });
              if (!r2.ok) throw new Error(`${r2.status}: ${await r2.text()}`);
            } else {
              throw new Error(`${res.status}: ${await res.text()}`);
            }
          }
          const data = await res.json().catch(() => ({}));
          this.showToast('success', '📤 Uploaded', `${data.path} (${this.formatFileSize(data.size || 0)})`);
        }
        await this.loadProjectFilesDir(this.projectFiles.cwd);
      } catch (e) {
        this.showToast('error', 'Upload failed', e.message || String(e));
      } finally {
        this.projectUploadBusy = false;
        event.target.value = '';
      }
    },

    formatRelativeTime(epochSec) {
      if (!epochSec) return '';
      const now = Date.now() / 1000;
      const diff = now - epochSec;
      if (diff < 60) return 'ora';
      if (diff < 3600) return `${Math.floor(diff/60)} min fa`;
      if (diff < 86400) return `${Math.floor(diff/3600)}h fa`;
      if (diff < 604800) return `${Math.floor(diff/86400)}g fa`;
      const d = new Date(epochSec * 1000);
      return d.toISOString().substring(0, 10);
    },

    // ===== Fase 22.9 — Hub file browser =====
    async openHubFiles(initialDir = 'files') {
      this.view = 'hub-files';
      this.hubFiles.selectedFile = '';
      this.hubFiles.fileContent = '';
      this.hubFiles.error = '';
      this.hubFiles.editMode = false;
      this.hubFiles.dirty = false;
      await this.loadHubFilesDir(initialDir);
      this.refreshIcons();
    },

    async loadHubFilesDir(path) {
      this.hubFiles.loading = true;
      this.hubFiles.error = '';
      try {
        const params = new URLSearchParams({ path: path || '' });
        const data = await this.fetchJson(`/api/hub/files?${params}`);
        if (data && data.type === 'dir') {
          this.hubFiles.cwd = data.path || '';
          this.hubFiles.entries = data.entries || [];
        } else if (data && data.error) {
          this.hubFiles.error = data.error;
        }
      } catch (e) {
        this.hubFiles.error = e.message || 'load failed';
      } finally {
        this.hubFiles.loading = false;
      }
    },

    initHubMonacoEditor(hostEl) {
      if (!hostEl) return;
      if (window._swkMonacoEditor && window._swkMonacoHost === hostEl) {
        this._monacoLoadFile(this.hubFiles.selectedFile, this.hubFiles.fileContent);
        return;
      }
      this._ensureMonaco().then((monaco) => {
        if (!hostEl.isConnected) return;
        if (window._swkMonacoEditor) {
          try { window._swkMonacoEditor.dispose(); } catch (e) {}
        }
        if (window._swkMonacoResizeObs) {
          try { window._swkMonacoResizeObs.disconnect(); } catch (e) {}
        }
        const language = this._monacoLanguageFor(this.hubFiles.selectedFile);
        const editor = monaco.editor.create(hostEl, {
          value: this.hubFiles.fileContent || '',
          language,
          theme: this._monacoTheme(),
          readOnly: !this.hubFiles.editMode,
          automaticLayout: false,
          fontSize: 13,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          wordWrap: 'on',
          renderWhitespace: 'none',
          folding: false,
          tabSize: 2,
        });
        window._swkMonacoEditor = editor;
        window._swkMonacoHost = hostEl;
        try {
          const ro = new ResizeObserver(() => {
            if (window._swkMonacoEditor === editor) editor.layout();
          });
          ro.observe(hostEl);
          window._swkMonacoResizeObs = ro;
        } catch (e) {}
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
          if (this.hubFiles.editMode && this.hubFiles.dirty) this.saveHubFile();
        });
        editor.onDidChangeModelContent(() => {
          const v = editor.getValue();
          this.hubFiles.currentValue = v;
          this.hubFiles.dirty = (v !== this.hubFiles.fileContent);
        });
        if (!window._swkMonacoThemeWatched) {
          this.$watch('theme', () => {
            monaco.editor.setTheme(this._monacoTheme());
          });
          window._swkMonacoThemeWatched = true;
        }
      }).catch((e) => {
        console.error('[hub-monaco] load failed', e);
        this.hubFiles.error = 'Monaco editor failed to load';
      });
    },

    async openHubFile(filename) {
      const filePath = this.hubFiles.cwd ? `${this.hubFiles.cwd}/${filename}` : filename;
      if (this.hubFiles.dirty && !confirm('File has unsaved changes. Continue?')) return;
      this.hubFiles.loading = true;
      this.hubFiles.error = '';
      this.hubFiles.editMode = false;
      this.hubFiles.dirty = false;
      this.hubFiles.tooLarge = false;
      try {
        const params = new URLSearchParams({ path: filePath });
        const data = await this.fetchJson(`/api/hub/files?${params}`);
        if (data && data.type === 'file') {
          this.hubFiles.selectedFile = filePath;
          this.hubFiles.fileContent = data.content || data.preview || '';
          this.hubFiles.currentValue = this.hubFiles.fileContent;
          this.hubFiles.fileSize = data.size || 0;
          this.hubFiles.tooLarge = !!(data.error && /too large/i.test(data.error));
          if (data.error) this.hubFiles.error = data.error;
          // Riusa Monaco editor pattern
          if (window._swkMonacoEditor) {
            this._monacoLoadFile(filePath, this.hubFiles.fileContent);
          }
        }
      } catch (e) {
        this.hubFiles.error = e.message || 'load failed';
      } finally {
        this.hubFiles.loading = false;
      }
    },

    enterHubDir(name) {
      const newPath = this.hubFiles.cwd ? `${this.hubFiles.cwd}/${name}` : name;
      this.hubFiles.selectedFile = '';
      this.hubFiles.fileContent = '';
      this.loadHubFilesDir(newPath);
    },

    goUpHubDir() {
      if (!this.hubFiles.cwd) return;
      const parts = this.hubFiles.cwd.split('/');
      parts.pop();
      const newPath = parts.join('/');
      this.hubFiles.selectedFile = '';
      this.hubFiles.fileContent = '';
      this.loadHubFilesDir(newPath);
    },

    toggleHubEdit(on) {
      this.hubFiles.editMode = !!on;
      if (window._swkMonacoEditor) {
        window._swkMonacoEditor.updateOptions({ readOnly: !on });
        if (on) this.$nextTick(() => { try { window._swkMonacoEditor.focus(); } catch (e) {} });
      }
    },

    cancelHubEdit() {
      if (this.hubFiles.dirty && !confirm('Discard unsaved changes?')) return;
      if (window._swkMonacoEditor) window._swkMonacoEditor.setValue(this.hubFiles.fileContent || '');
      this.hubFiles.dirty = false;
      this.toggleHubEdit(false);
    },

    async saveHubFile() {
      if (this.hubFiles.saving) return;
      const newVal = window._swkMonacoEditor ? window._swkMonacoEditor.getValue() : this.hubFiles.currentValue;
      this.hubFiles.saving = true;
      try {
        const res = await fetch('/api/hub/file', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: this.hubFiles.selectedFile, content: newVal }),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        const data = await res.json();
        this.hubFiles.fileContent = newVal;
        this.hubFiles.fileSize = data.size || 0;
        this.hubFiles.dirty = false;
        this.showToast('success', '💾 Saved', `${this.hubFiles.selectedFile}${data.backup ? ' · backup ' + data.backup : ''}`);
        this.toggleHubEdit(false);
      } catch (e) {
        this.showToast('error', 'Save failed', e.message || String(e));
      } finally {
        this.hubFiles.saving = false;
      }
    },

    async archiveWorkspace(name) {
      if (!confirm(`Archive workspace "${name}"?\n\nMoved to workspaces/.archive/ (recoverable manually). Removed from the registry.`)) return;
      try {
        const res = await fetch(`/api/workspaces/${encodeURIComponent(name)}/archive`, { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
        const data = await res.json();
        this.showToast('success', '📦 Archived', `${name} → ${data.archived_to}`);
        await this.loadRegistry();
        if (this.workspaceScope === `project:${name}`) this.switchToHub();
      } catch (e) {
        this.showToast('error', 'Archive failed', e.message || String(e));
      }
    },

    async deleteWorkspace(name, kind) {
      const warn = kind === 'external'
        ? `Remove the LINK to workspace "${name}"?\n\nThe original .anjawiki (outside the hub) is NOT touched.`
        : `PERMANENTLY DELETE workspace "${name}"?\n\nAll files (files/, data/, scripts/, wiki/, agents/) will be removed.\nThis action is IRREVERSIBLE.`;
      if (!confirm(warn)) return;
      try {
        const res = await fetch(`/api/workspaces/${encodeURIComponent(name)}/delete`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
        const data = await res.json();
        this.showToast('success', kind === 'external' ? '🔗 Unlinked' : '🗑 Deleted', name);
        await this.loadRegistry();
        if (this.workspaceScope === `project:${name}`) this.switchToHub();
      } catch (e) {
        this.showToast('error', 'Delete failed', e.message || String(e));
      }
    },

    async submitWorkspaceWizard() {
      if (this.wsWizard.busy) return;
      this.wsWizard.busy = true;
      this.wsWizard.error = '';
      try {
        const res = await fetch('/api/workspaces/create', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: this.wsWizard.name.trim(),
            ws_type: this.wsWizard.ws_type,
            responsabile_name: this.wsWizard.responsabile_name.trim(),
            role_description: this.wsWizard.role_description.trim(),
            responsabile_provider: this.wsWizard.responsabile_provider,
            responsabile_model: this.wsWizard.responsabile_model.trim() || 'sonnet',
            responsabile_effort: this.wsWizard.responsabile_effort || null,
          }),
        });
        if (!res.ok) {
          const txt = await res.text();
          throw new Error(`HTTP ${res.status}: ${txt}`);
        }
        const data = await res.json();
        this.showToast('success', '✅ Workspace created',
          `${data.slug} (responsabile: ${data.responsabile_slug})`);
        // Reload registry + switcher
        await this.loadRegistry();
        this.closeWorkspaceWizard();
        // Auto-switch al nuovo workspace
        this.switchToProject(data.slug);
      } catch (e) {
        this.wsWizard.error = e.message || String(e);
      } finally {
        this.wsWizard.busy = false;
      }
    },

    // ===== Fase 13 Workspace — Project files browser =====
    async openProjectFiles(name) {
      this.view = 'project-files';
      this.projectFiles.project = name;
      this.projectFiles.cwd = '';
      this.projectFiles.selectedFile = '';
      this.projectFiles.fileContent = '';
      this.projectFiles.error = '';
      await this.loadProjectFilesDir('');
      this.refreshIcons();
    },

    async loadProjectFilesDir(path) {
      this.projectFiles.loading = true;
      this.projectFiles.error = '';
      try {
        const params = new URLSearchParams({ project: this.projectFiles.project, path: path || '' });
        const data = await this.fetchJson(`/api/project/files?${params}`);
        if (data && data.type === 'dir') {
          this.projectFiles.cwd = data.path || '';
          this.projectFiles.entries = data.entries || [];
        } else if (data && data.error) {
          this.projectFiles.error = data.error;
        }
      } catch (e) {
        this.projectFiles.error = e.message || 'load failed';
      } finally {
        this.projectFiles.loading = false;
      }
      this.refreshIcons();
    },

    async openProjectFile(filename) {
      const filePath = this.projectFiles.cwd ? `${this.projectFiles.cwd}/${filename}` : filename;
      // Confirm se dirty
      if (this.projectFiles.dirty) {
        if (!confirm('File has unsaved changes. Continue?')) return;
      }
      this.projectFiles.loading = true;
      this.projectFiles.error = '';
      this.projectFiles.editMode = false;
      this.projectFiles.dirty = false;
      this.projectFiles.tooLarge = false;
      try {
        const params = new URLSearchParams({ project: this.projectFiles.project, path: filePath });
        const data = await this.fetchJson(`/api/project/files?${params}`);
        if (data && data.type === 'file') {
          this.projectFiles.selectedFile = filePath;
          this.projectFiles.fileContent = data.content || data.preview || '';
          this.projectFiles.currentValue = this.projectFiles.fileContent;
          this.projectFiles.fileSize = data.size || 0;
          this.projectFiles.tooLarge = !!(data.error && /too large/i.test(data.error));
          if (data.error) this.projectFiles.error = data.error;
          // Update Monaco model se editor già montato
          this._monacoLoadFile(filePath, this.projectFiles.fileContent);
        }
      } catch (e) {
        this.projectFiles.error = e.message || 'load failed';
      } finally {
        this.projectFiles.loading = false;
      }
    },

    // ============================================================
    // Monaco editor integration (Fase 4-IDE+ L1)
    // ============================================================

    _monacoLanguageFor(path) {
      const ext = (path || '').toLowerCase().split('.').pop();
      const map = {
        py: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript',
        ts: 'typescript', tsx: 'typescript', jsx: 'javascript',
        md: 'markdown', mdx: 'markdown',
        json: 'json', jsonc: 'json',
        yaml: 'yaml', yml: 'yaml',
        toml: 'ini', ini: 'ini', cfg: 'ini',
        sh: 'shell', bash: 'shell', zsh: 'shell',
        html: 'html', htm: 'html', xml: 'xml', svg: 'xml',
        css: 'css', scss: 'scss', less: 'less',
        go: 'go', rs: 'rust', rb: 'ruby', java: 'java', kt: 'kotlin',
        c: 'c', cpp: 'cpp', cc: 'cpp', h: 'cpp', hpp: 'cpp',
        sql: 'sql', php: 'php', swift: 'swift',
        dockerfile: 'dockerfile', env: 'shell', gitignore: 'plaintext',
      };
      if (path && /^Dockerfile/i.test(path.split('/').pop())) return 'dockerfile';
      if (path && /^(README|CHANGELOG|LICENSE|CONTRIBUTING)/i.test(path.split('/').pop())) return 'markdown';
      return map[ext] || 'plaintext';
    },

    _monacoTheme() {
      return (this.theme === 'dark') ? 'vs-dark' : 'vs';
    },

    initMonacoEditor(hostEl) {
      if (!hostEl) return;
      // Guardia anti-doppio init: se l'editor esiste già ed è agganciato a questo host, riusa
      if (window._swkMonacoEditor && window._swkMonacoHost === hostEl) {
        this._monacoLoadFile(this.projectFiles.selectedFile, this.projectFiles.fileContent);
        return;
      }
      // Lazy load Monaco una volta
      this._ensureMonaco().then((monaco) => {
        if (!hostEl.isConnected) return;
        // Dispose previous editor + ResizeObserver se esiste (cambio host)
        if (window._swkMonacoEditor) {
          try { window._swkMonacoEditor.dispose(); } catch (e) {}
        }
        if (window._swkMonacoResizeObs) {
          try { window._swkMonacoResizeObs.disconnect(); } catch (e) {}
        }
        const language = this._monacoLanguageFor(this.projectFiles.selectedFile);
        const editor = monaco.editor.create(hostEl, {
          value: this.projectFiles.fileContent || '',
          language,
          theme: this._monacoTheme(),
          readOnly: !this.projectFiles.editMode,
          automaticLayout: false,   // usiamo ResizeObserver, più leggero del polling
          fontSize: 13,
          minimap: { enabled: false }, // off di default per perf, toggleable in L2
          scrollBeyondLastLine: false,
          wordWrap: 'on',
          renderWhitespace: 'none',
          renderLineHighlight: 'line',
          occurrencesHighlight: 'off',
          folding: false,
          tabSize: 2,
          fixedOverflowWidgets: true,
        });
        window._swkMonacoEditor = editor;
        window._swkMonacoHost = hostEl;
        // ResizeObserver invece di automaticLayout (poll 30ms)
        try {
          const ro = new ResizeObserver(() => {
            if (window._swkMonacoEditor === editor) editor.layout();
          });
          ro.observe(hostEl);
          window._swkMonacoResizeObs = ro;
        } catch (e) { /* fallback: nessun resize tracking */ }
        // Cmd+S shortcut
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
          if (this.projectFiles.editMode && this.projectFiles.dirty) this.showSaveDiff();
        });
        // Track dirty
        editor.onDidChangeModelContent(() => {
          const v = editor.getValue();
          this.projectFiles.currentValue = v;
          this.projectFiles.dirty = (v !== this.projectFiles.fileContent);
        });
        // Watch theme — UNA SOLA volta (guardia globale)
        if (!window._swkMonacoThemeWatched) {
          this.$watch('theme', () => {
            monaco.editor.setTheme(this._monacoTheme());
          });
          window._swkMonacoThemeWatched = true;
        }
      }).catch((e) => {
        console.error('[monaco] load failed', e);
        this.projectFiles.error = 'Monaco editor failed to load';
      });
    },

    _monacoLoadFile(path, content) {
      if (!window._swkMonacoEditor || !window._swkMonaco) return;
      const monaco = window._swkMonaco;
      const editor = window._swkMonacoEditor;
      const lang = this._monacoLanguageFor(path);
      const model = monaco.editor.createModel(content || '', lang);
      const old = editor.getModel();
      editor.setModel(model);
      if (old) try { old.dispose(); } catch (e) {}
      editor.updateOptions({ readOnly: !this.projectFiles.editMode });
    },

    _ensureMonaco() {
      if (window._swkMonaco) return Promise.resolve(window._swkMonaco);
      return new Promise((resolve, reject) => {
        if (!window.require) return reject(new Error('AMD loader not available'));
        window.require(['vs/editor/editor.main'], () => {
          window._swkMonaco = window.monaco;
          resolve(window.monaco);
        }, reject);
      });
    },

    toggleEditMode(on) {
      this.projectFiles.editMode = !!on;
      if (window._swkMonacoEditor) {
        window._swkMonacoEditor.updateOptions({ readOnly: !on });
        if (on) {
          this.$nextTick(() => { try { window._swkMonacoEditor.focus(); } catch (e) {} });
        }
      }
    },

    cancelEdit() {
      if (this.projectFiles.dirty && !confirm('Discard unsaved changes?')) return;
      if (window._swkMonacoEditor) {
        window._swkMonacoEditor.setValue(this.projectFiles.fileContent || '');
      }
      this.projectFiles.dirty = false;
      this.toggleEditMode(false);
    },

    showSaveDiff() {
      const newVal = window._swkMonacoEditor ? window._swkMonacoEditor.getValue() : this.projectFiles.currentValue;
      this.saveDiff.path = this.projectFiles.selectedFile;
      this.saveDiff.originalSize = (this.projectFiles.fileContent || '').length;
      this.saveDiff.newSize = (newVal || '').length;
      this.saveDiff.open = true;
      this.refreshIcons();
    },

    cancelSaveDiff() {
      this.saveDiff.open = false;
      if (window._swkMonacoDiffEditor) {
        try { window._swkMonacoDiffEditor.dispose(); } catch (e) {}
        window._swkMonacoDiffEditor = null;
      }
    },

    initDiffEditor(hostEl) {
      if (!hostEl) return;
      this._ensureMonaco().then((monaco) => {
        if (!hostEl.isConnected || !this.saveDiff.open) return;
        if (window._swkMonacoDiffEditor) {
          try { window._swkMonacoDiffEditor.dispose(); } catch (e) {}
        }
        const language = this._monacoLanguageFor(this.saveDiff.path);
        const original = monaco.editor.createModel(this.projectFiles.fileContent || '', language);
        const newVal = window._swkMonacoEditor ? window._swkMonacoEditor.getValue() : this.projectFiles.currentValue;
        const modified = monaco.editor.createModel(newVal || '', language);
        const diff = monaco.editor.createDiffEditor(hostEl, {
          theme: this._monacoTheme(),
          automaticLayout: true,
          readOnly: true,
          fontSize: 12,
          renderSideBySide: true,
        });
        diff.setModel({ original, modified });
        window._swkMonacoDiffEditor = diff;
      }).catch((e) => console.error('[monaco-diff] load failed', e));
    },

    async confirmSaveDiff() {
      if (this.projectFiles.saving) return;
      const newVal = window._swkMonacoEditor ? window._swkMonacoEditor.getValue() : this.projectFiles.currentValue;
      this.projectFiles.saving = true;
      try {
        const res = await fetch('/api/project/file', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project: this.projectFiles.project,
            path: this.projectFiles.selectedFile,
            content: newVal,
            expected_size: this.projectFiles.fileSize,
          }),
        });
        if (!res.ok) {
          const txt = await res.text();
          throw new Error(`save failed (${res.status}): ${txt}`);
        }
        const data = await res.json();
        this.projectFiles.fileContent = newVal;
        this.projectFiles.fileSize = data.size || this.projectFiles.fileSize;
        this.projectFiles.dirty = false;
        this.showToast('success', '💾 Saved', `${this.projectFiles.selectedFile}${data.backup ? ' · backup ' + data.backup : ''}`);
        this.cancelSaveDiff();
        this.toggleEditMode(false);
      } catch (e) {
        this.showToast('error', 'Save failed', e.message || String(e));
      } finally {
        this.projectFiles.saving = false;
      }
    },

    // ============================================================
    // Chat-with-file sidepanel (Fase 4-IDE+ L1.5)
    // ============================================================

    toggleFileChat() {
      this.fileChat.open = !this.fileChat.open;
      if (this.fileChat.open) {
        this._initFileChatIfNeeded();
        this.refreshIcons();
      }
      // Monaco è full-width overlay (no resize necessario) ma chiamo layout() di safety
      this.$nextTick(() => {
        try { window._swkMonacoEditor && window._swkMonacoEditor.layout(); } catch (e) {}
      });
    },

    _initFileChatIfNeeded() {
      const key = `${this.projectFiles.project}:${this.projectFiles.selectedFile}`;
      if (this.fileChat._key === key) return;
      this.fileChat._key = key;
      this.fileChat.messages = [];
      this.fileChat.convId = `file-${this.projectFiles.project}-${this._slugifyForId(this.projectFiles.selectedFile)}-${Date.now().toString(36)}`;
      this.fileChat.contextBytes = (this.projectFiles.fileContent || '').length;
      // Lazy load project agents
      if (this.projectFiles.project && (!this.projectAgents || this.projectAgents.length === 0)) {
        if (typeof this.loadProjectAgents === 'function') {
          this.loadProjectAgents().catch(() => {});
        }
      }
    },

    _slugifyForId(s) {
      return (s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
    },

    newFileChat() {
      this.fileChat.messages = [];
      this.fileChat.convId = `file-${this.projectFiles.project}-${this._slugifyForId(this.projectFiles.selectedFile)}-${Date.now().toString(36)}`;
      if (window._swkFileChatWs) {
        try { window._swkFileChatWs.close(); } catch (e) {}
        window._swkFileChatWs = null;
      }
    },

    includeSelectionInChat() {
      const editor = window._swkMonacoEditor;
      if (!editor) return;
      const sel = editor.getSelection();
      const model = editor.getModel();
      if (!sel || !model || sel.isEmpty()) {
        this.showToast('info', 'No selection', 'Select text in the editor first');
        return;
      }
      const text = model.getValueInRange(sel);
      const startL = sel.startLineNumber;
      const endL = sel.endLineNumber;
      const ref = `${this.projectFiles.selectedFile}:${startL}${startL !== endL ? '-' + endL : ''}`;
      const block = '\n```\n' + text + '\n```\n';
      const prefix = this.fileChat.input ? this.fileChat.input + '\n\n' : '';
      this.fileChat.input = `${prefix}> ${ref}${block}`;
    },

    async sendFileChat() {
      const txt = (this.fileChat.input || '').trim();
      if (!txt || this.fileChat.streaming) return;
      this._initFileChatIfNeeded();

      // Snapshot pre-edit per Undo (L1.5.3)
      this.fileChat._snapshot = this.projectFiles.fileContent || '';

      // Push user message
      this.fileChat.messages.push({ role: 'user', content: txt });
      this.fileChat.messages.push({ role: 'assistant', content: '' });
      this.fileChat.input = '';
      this.fileChat.streaming = true;
      this._fileChatScrollDown();

      // Open dedicated WS if needed
      if (!window._swkFileChatWs || window._swkFileChatWs.readyState !== WebSocket.OPEN) {
        await this._fileChatConnect();
      }
      if (!window._swkFileChatWs || window._swkFileChatWs.readyState !== WebSocket.OPEN) {
        this.fileChat.streaming = false;
        this.showToast('error', 'WS failed', 'Chat-file connection not available');
        return;
      }

      // Cursor info
      let cursor = null;
      try {
        const ed = window._swkMonacoEditor;
        if (ed) {
          const pos = ed.getPosition();
          if (pos) cursor = { line: pos.lineNumber, column: pos.column };
        }
      } catch (e) {}

      // Build scope: agent or project
      let scope;
      if (this.fileChat.agentScope && this.fileChat.agentScope.startsWith('agent:')) {
        scope = this.fileChat.agentScope;
      } else {
        scope = `project:${this.projectFiles.project}`;
      }

      const payload = {
        message: txt,
        conversation_id: this.fileChat.convId,
        scope,
        provider: this.hubDefaults && this.hubDefaults.provider ? this.hubDefaults.provider : undefined,
        model: this.hubDefaults && this.hubDefaults.model ? this.hubDefaults.model : undefined,
        file_context: {
          project: this.projectFiles.project,
          path: this.projectFiles.selectedFile,
          content: this.projectFiles.fileContent || '',
          language: this._monacoLanguageFor(this.projectFiles.selectedFile),
          cursor,
        },
      };
      try {
        window._swkFileChatWs.send(JSON.stringify(payload));
      } catch (e) {
        this.fileChat.streaming = false;
        this.showToast('error', 'Send failed', e.message || String(e));
      }
    },

    _fileChatConnect() {
      return new Promise((resolve) => {
        try {
          const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
          const ws = new WebSocket(`${proto}//${window.location.host}/api/chat`);
          window._swkFileChatWs = ws;
          ws.onopen = () => resolve();
          ws.onclose = () => {
            window._swkFileChatWs = null;
            if (this.fileChat.streaming) {
              this.fileChat.streaming = false;
            }
          };
          ws.onerror = (e) => {
            console.error('[file-chat] ws error', e);
            resolve();
          };
          ws.onmessage = (ev) => this._fileChatOnMessage(ev);
          // Safety timeout
          setTimeout(() => resolve(), 1500);
        } catch (e) {
          console.error('[file-chat] connect err', e);
          resolve();
        }
      });
    },

    _fileChatOnMessage(event) {
      let data;
      try { data = JSON.parse(event.data); } catch (e) { return; }
      const msgs = this.fileChat.messages;
      const last = msgs.length > 0 ? msgs[msgs.length - 1] : null;
      if (data.type === 'text') {
        if (last && last.role === 'assistant') {
          last.content += data.content;
        } else {
          msgs.push({ role: 'assistant', content: data.content });
        }
        this._fileChatScrollDown();
      } else if (data.type === 'tool_use') {
        msgs.push({ role: 'tool', content: `${data.name}${data.input && data.input.file_path ? ' → ' + data.input.file_path : ''}` });
        // Detect Edit/Write on aperto file → segna per reload post-done
        const ti = data.input || {};
        const targetPath = ti.file_path || '';
        const curAbs = `${(this.projectFiles.projectRoot || '').replace(/\/$/, '')}/${this.projectFiles.selectedFile}`;
        if ((data.name === 'Edit' || data.name === 'Write') &&
            (targetPath === curAbs || targetPath.endsWith('/' + this.projectFiles.selectedFile))) {
          this.fileChat._pendingReload = true;
        }
        this._fileChatScrollDown();
        this.refreshIcons();
      } else if (data.type === 'done') {
        this.fileChat.streaming = false;
        if (this.fileChat._pendingReload) {
          this.fileChat._pendingReload = false;
          // Reload file content (l'agent ha modificato via Edit/Write)
          this._reloadOpenedFile();
        }
        this._fileChatScrollDown();
      } else if (data.type === 'error') {
        this.fileChat.streaming = false;
        msgs.push({ role: 'tool', content: `❌ ${data.message}` });
        this._fileChatScrollDown();
      }
    },

    async _reloadOpenedFile() {
      if (!this.projectFiles.project || !this.projectFiles.selectedFile) return;
      const oldContent = this.fileChat._snapshot || '';
      try {
        const params = new URLSearchParams({ project: this.projectFiles.project, path: this.projectFiles.selectedFile });
        const data = await this.fetchJson(`/api/project/files?${params}`);
        if (data && data.type === 'file') {
          const newContent = data.content || '';
          this.projectFiles.fileContent = newContent;
          this.projectFiles.fileSize = data.size || 0;
          this.projectFiles.dirty = false;
          if (window._swkMonacoEditor) {
            window._swkMonacoEditor.setValue(newContent);
          }
          // L1.5.3 — review modal post-hoc se Trust OFF e contenuto cambiato
          if (!this.fileChat.trust && oldContent !== newContent) {
            this.fileReview.path = this.projectFiles.selectedFile;
            this.fileReview.oldContent = oldContent;
            this.fileReview.newContent = newContent;
            this.fileReview.open = true;
            this.refreshIcons();
          } else {
            this.showToast('success', '📝 File updated', 'Agent modified the file');
          }
        }
      } catch (e) {
        console.error('[file-chat] reload err', e);
      }
    },

    closeFileReview() {
      this.fileReview.open = false;
      if (window._swkMonacoReviewEditor) {
        try { window._swkMonacoReviewEditor.dispose(); } catch (e) {}
        window._swkMonacoReviewEditor = null;
      }
    },

    keepFileReview() {
      this.showToast('success', '✅ Changes kept', this.fileReview.path);
      this.closeFileReview();
    },

    async undoFileReview() {
      const path = this.fileReview.path;
      const oldContent = this.fileReview.oldContent;
      if (!path) { this.closeFileReview(); return; }
      try {
        const res = await fetch('/api/project/file', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project: this.projectFiles.project,
            path,
            content: oldContent,
          }),
        });
        if (!res.ok) {
          const txt = await res.text();
          throw new Error(`undo failed (${res.status}): ${txt}`);
        }
        const data = await res.json();
        this.projectFiles.fileContent = oldContent;
        this.projectFiles.fileSize = data.size || 0;
        if (window._swkMonacoEditor) {
          window._swkMonacoEditor.setValue(oldContent);
        }
        this.showToast('success', '↶ Undo', `${path} restored`);
        this.closeFileReview();
      } catch (e) {
        this.showToast('error', 'Undo failed', e.message || String(e));
      }
    },

    initReviewEditor(hostEl) {
      if (!hostEl) return;
      this._ensureMonaco().then((monaco) => {
        if (!hostEl.isConnected || !this.fileReview.open) return;
        if (window._swkMonacoReviewEditor) {
          try { window._swkMonacoReviewEditor.dispose(); } catch (e) {}
        }
        const language = this._monacoLanguageFor(this.fileReview.path);
        const original = monaco.editor.createModel(this.fileReview.oldContent || '', language);
        const modified = monaco.editor.createModel(this.fileReview.newContent || '', language);
        const diff = monaco.editor.createDiffEditor(hostEl, {
          theme: this._monacoTheme(),
          automaticLayout: true,
          readOnly: true,
          fontSize: 12,
          renderSideBySide: true,
        });
        diff.setModel({ original, modified });
        window._swkMonacoReviewEditor = diff;
      }).catch((e) => console.error('[file-review] monaco err', e));
    },

    _fileChatScrollDown() {
      this.$nextTick(() => {
        const el = this.$refs.fileChatScroll;
        if (el) el.scrollTop = el.scrollHeight;
      });
    },

    enterProjectDir(name) {
      const newPath = this.projectFiles.cwd ? `${this.projectFiles.cwd}/${name}` : name;
      this.projectFiles.selectedFile = '';
      this.projectFiles.fileContent = '';
      this.loadProjectFilesDir(newPath);
    },

    goUpProjectDir() {
      if (!this.projectFiles.cwd) return;
      const parts = this.projectFiles.cwd.split('/');
      parts.pop();
      const newPath = parts.join('/');
      this.projectFiles.selectedFile = '';
      this.projectFiles.fileContent = '';
      this.loadProjectFilesDir(newPath);
    },

    formatFileSize(bytes) {
      if (!bytes) return '0';
      if (bytes < 1024) return `${bytes}B`;
      if (bytes < 1024 * 1024) return `${(bytes/1024).toFixed(1)}K`;
      return `${(bytes/(1024*1024)).toFixed(1)}M`;
    },


    refreshIcons() {
      this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
    },

    modelsCatalogLoaded: {},  // {provider: true} flag fetch fatto

    async _ensureModelsFor(provider, force = false) {
      // Carica dinamicamente i modelli da backend (cache 1h server-side)
      if (!force && this.modelsCatalogLoaded[provider]) return;
      try {
        const url = `/api/providers/${encodeURIComponent(provider)}/models${force ? '?refresh=1' : ''}`;
        const data = await this.fetchJson(url);
        if (data && Array.isArray(data.models) && data.models.length) {
          this.modelsCatalog[provider] = data.models;
          this.modelsCatalogLoaded[provider] = true;
        }
      } catch (e) {
        // fallback alle liste hardcoded
      }
    },

    async refreshModels(provider) {
      await this._ensureModelsFor(provider, true);
    },

    // ===== Fase 7v.c — Unified model picker =====

    providerIcon(p) {
      const m = {
        claude: '◆',
        openai_oauth: '◆',
        openai: '◇',
        xai: '◇',
        openrouter: '◇',
        gemini: '◇',
        mistral: '◇',
        groq: '◇',
        ollama: '🦙',
      };
      return m[p] || '○';
    },

    providerShortLabel(p) {
      const m = {
        claude: 'Claude sub',
        openai_oauth: 'ChatGPT sub',
        openai: 'OpenAI',
        xai: 'xAI',
        openrouter: 'OpenRouter',
        gemini: 'Gemini',
        mistral: 'Mistral',
        groq: 'Groq',
        ollama: 'Ollama',
      };
      return m[p] || p;
    },

    selectModel(providerId, model) {
      this.selectedProvider = providerId;
      this.selectedModel = model;
      this.modelPickerOpen = false;
      if (providerId !== 'claude') {
        this.selectedEffort = '';
      }
    },

    filteredUnifiedGroups() {
      const q = (this.modelPickerSearch || '').toLowerCase().trim();
      if (!q) return this.unifiedModelGroups;
      return this.unifiedModelGroups
        .map(g => ({
          ...g,
          models: g.models.filter(m =>
            m.toLowerCase().includes(q) ||
            g.label.toLowerCase().includes(q) ||
            g.providerId.toLowerCase().includes(q)
          ),
        }))
        .filter(g => g.models.length > 0);
    },

    async buildUnifiedModels() {
      // Costruisce groups partendo da state correnti dei provider
      const groups = [];

      // Claude (subscription o API key — sempre disponibile)
      const claudeSub = !!this.claudeOauthState?.subscription_active && !this.claudeOauthState?.api_key_set;
      groups.push({
        providerId: 'claude',
        label: claudeSub ? 'Claude (subscription)' : 'Claude',
        icon: '◆',
        models: ['sonnet', 'opus', 'fable', 'haiku'],
      });

      // OpenAI ChatGPT subscription
      if (this.openaiOauthState?.anja_enabled && this.openaiOauthState?.supported_models?.length) {
        groups.push({
          providerId: 'openai_oauth',
          label: 'ChatGPT (subscription)',
          icon: '◆',
          models: this.openaiOauthState.supported_models,
        });
      }

      // Cloud API providers — solo quelli con key configurata
      const configuredProviders = (this.settingsState?.providers || [])
        .filter(p => p.configured)
        .map(p => p.id);
      const apiProviders = [
        { id: 'openai', label: 'OpenAI (API)' },
        { id: 'xai', label: 'xAI (API)' },
        { id: 'openrouter', label: 'OpenRouter' },
        { id: 'gemini', label: 'Gemini (API)' },
        { id: 'mistral', label: 'Mistral (API)' },
        { id: 'groq', label: 'Groq (API)' },
      ];
      for (const ap of apiProviders) {
        // Skip se non configurato (eccetto openai/xai/openrouter che sono sempre nei dropdown legacy)
        if (!configuredProviders.includes(ap.id)) continue;
        await this._ensureModelsFor(ap.id);
        const models = this.modelsCatalog[ap.id] || [];
        if (models.length) {
          groups.push({ providerId: ap.id, label: ap.label, icon: '◇', models });
        }
      }

      // Ollama local
      if (this.ollamaState?.enabled && this.ollamaState?.models?.length) {
        groups.push({
          providerId: 'ollama',
          label: 'Ollama (local)',
          icon: '🦙',
          models: this.ollamaState.models.map(m => m.name),
        });
      }

      this.unifiedModelGroups = groups;
    },

    async ensureUnifiedModelsReady() {
      // Lazy: se groups vuoto o stale, carica state provider + rebuild
      if (this.unifiedModelGroups.length > 0) return;
      // Load tutto in parallelo
      await Promise.all([
        this.loadSettings(),
        this.loadOllamaConfig(),
        this.loadOpenaiOauthStatus(),
        this.loadClaudeOauthStatus(),
      ]);
      await this.buildUnifiedModels();
      this.refreshIcons();
    },

    async refreshUnifiedModels() {
      this.unifiedModelsRefreshing = true;
      try {
        // Force refresh ollama + openai_oauth status, poi rebuild
        await this.refreshOllamaModels(true);
        await this.loadOpenaiOauthStatus();
        // Force refresh cache modelli per ogni provider configurato
        const configured = (this.settingsState?.providers || [])
          .filter(p => p.configured).map(p => p.id);
        for (const pid of configured) {
          await this._ensureModelsFor(pid, true);
        }
        await this.buildUnifiedModels();
      } finally {
        this.unifiedModelsRefreshing = false;
      }
    },

    async onProviderChange() {
      await this._ensureModelsFor(this.selectedProvider);
      const list = this.modelsCatalog[this.selectedProvider] || [];
      if (list.length && !list.includes(this.selectedModel)) {
        this.selectedModel = list[0];
      }
      if (this.selectedProvider !== 'claude') {
        this.selectedEffort = '';
      }
    },

    async onWizardProviderChange() {
      await this._ensureModelsFor(this.wizardForm.provider);
      const list = this.modelsCatalog[this.wizardForm.provider] || [];
      if (list.length && !list.includes(this.wizardForm.model)) {
        this.wizardForm.model = list[0];
      }
      if (this.wizardForm.provider !== 'claude') {
        this.wizardForm.effort = 'off';
      }
    },

    saveMediaGenPref() {
      try {
        localStorage.setItem('anja_enable_media_gen', this.enableImageGen ? '1' : '0');
      } catch (e) { /* ignore */ }
    },

    // Fase 23.b — Media model picker
    mediaModelIcon(slug) {
      if (!slug) return '🤖';
      const lower = (slug || '').toLowerCase();
      // Video models
      if (lower.includes('veo') || lower.includes('sora') || lower.includes('kling') ||
          lower.includes('seedance') || lower.includes('wan') || lower.includes('hailuo') ||
          lower.includes('imagine-video')) {
        return '🎬';
      }
      // Image
      return '📷';
    },

    async loadMediaModels(force) {
      this.mediaModelsLoading = true;
      try {
        const url = '/api/media-models' + (force ? '?refresh=1' : '');
        const data = await this.fetchJson(url);
        this.mediaModelsData = {
          image: data.image || [],
          video: data.video || [],
        };
        this.mediaModelsLoaded = true;
      } catch (e) {
        console.error('loadMediaModels failed', e);
      } finally {
        this.mediaModelsLoading = false;
        this.refreshIcons();
      }
    },

    selectMediaModel(slug) {
      this.mediaModel = slug || '';
      this.mediaPickerOpen = false;
      try {
        if (this.mediaModel) {
          localStorage.setItem('anja_media_model', this.mediaModel);
        } else {
          localStorage.removeItem('anja_media_model');
        }
      } catch (e) { /* ignore */ }
    },

    toggleTheme() {
      this.theme = this.theme === 'light' ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', this.theme);
      try { localStorage.setItem('anja_theme', this.theme); } catch (e) {}
      this.refreshIcons();
    },

    toggleSkin() {
      this.skin = this.skin === 'swebify' ? '' : 'swebify';
      if (this.skin) document.documentElement.setAttribute('data-skin', this.skin);
      else document.documentElement.removeAttribute('data-skin');
      try { localStorage.setItem('anja_skin', this.skin); } catch (e) {}
      this.refreshIcons();
    },

    // ===== API CALLS =====
    async fetchJson(path) {
      try {
        const r = await fetch(path);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return await r.json();
      } catch (e) {
        console.error(`fetchJson ${path}:`, e);
        return null;
      }
    },

    async fetchText(path) {
      try {
        const r = await fetch(path);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return await r.text();
      } catch (e) {
        console.error(`fetchText ${path}:`, e);
        return null;
      }
    },

    async loadRegistry() {
      this.loading.registry = true;
      const data = await this.fetchJson('/api/registry');
      if (data) {
        this.hubInfo = data.hub || this.hubInfo;
        this.projects = (data.projects || []).map(p => ({
          name: p.name,
          type: p.type || 'dev',
          tags: p.tags || [],
          totalPages: p.totalPages || 0,
          lastSyncShort: p.lastSync ? p.lastSync.substring(0, 16).replace('T', ' ') : '',
          lintIssues: p.lintIssues || 0,
          kind: p.kind || 'external',  // Fase 22
          responsabile: p.responsabile || null,
        }));
        this.crossAnalyses = data.crossAnalyses || [];
        this.recentActivity = data.recentActivity || [];
      }
      this.loading.registry = false;
      this.refreshIcons();
    },

    async loadHealth() {
      const data = await this.fetchJson('/api/health');
      if (data) {
        this.health = {
          errors: data.errors || 0,
          warnings: data.warnings || 0,
          suggestions: data.suggestions || 0,
        };
      }
    },

    async loadConversations() {
      // Fase 13 Workspace: carichiamo TUTTE le conv. Filter avviene via getter filteredConversations.
      const data = await this.fetchJson('/api/conversations');
      if (data && data.conversations) {
        this.conversations = data.conversations.map(c => ({
          id: c.id,
          title: c.title || '(senza titolo)',
          scope: c.scope,
          msg_count: c.msg_count || 0,
          modified: c.modified,
        }));
      }
      this.refreshIcons();
    },

    async loadFileTree(projectName) {
      if (!projectName) return;
      if (this.fileTreeProject === projectName) return;
      this.fileTreeProject = projectName;
      const data = await this.fetchJson(`/api/project/${encodeURIComponent(projectName)}/files`);
      if (data) {
        this.fileTree = data.tree || [];
        this.fileTreeRoot = data.root || '';
      }
      this.refreshIcons();
    },

    insertPathInChat(path) {
      // Append the path al campo input (utile per "Read file <path>")
      const sep = this.inputText && !this.inputText.endsWith(' ') ? ' ' : '';
      this.inputText += sep + '`' + path + '`';
    },

    toggleFolder(path) {
      this.fileTreeExpanded[path] = !this.fileTreeExpanded[path];
    },

    // Flatten the file tree in render order, respecting fileTreeExpanded state.
    // Returns array of {name, type, depth, path, size?, hasChildren}
    flatFileTree() {
      const result = [];
      const walk = (nodes, parentPath, depth) => {
        for (const n of (nodes || [])) {
          const path = parentPath ? `${parentPath}/${n.name}` : n.name;
          const hasChildren = n.type === 'dir' && Array.isArray(n.children) && n.children.length > 0;
          result.push({
            name: n.name,
            type: n.type,
            depth: depth,
            path: path,
            size: n.size,
            hasChildren: hasChildren,
            expanded: !!this.fileTreeExpanded[path],
          });
          if (n.type === 'dir' && this.fileTreeExpanded[path] && hasChildren) {
            walk(n.children, path, depth + 1);
          }
        }
      };
      walk(this.fileTree, '', 0);
      return result;
    },

    async loadProjectConversations(projectName) {
      if (!projectName) return;
      this.projectConversationsProject = projectName;
      const scope = `project:${projectName}`;
      const data = await this.fetchJson(`/api/conversations?scope=${encodeURIComponent(scope)}`);
      this.projectConversations = ((data && data.conversations) || []).map(c => ({
        id: c.id,
        title: c.title || '(senza titolo)',
        scope: c.scope,
        msg_count: c.msg_count || 0,
        modified: c.modified,
      }));
      this.refreshIcons();
    },

    async loadCurrentPage() {
      if (this.view !== 'viewer' || !this.currentProject) return;
      const cacheKey = `${this.currentProject}/${this.currentPage}`;
      if (this.pageCache[cacheKey] !== undefined) {
        this.currentPageContent = this.pageCache[cacheKey];
        return;
      }
      this.loading.page = true;
      this.currentPageContent = '';
      const text = await this.fetchText(`/api/project/${encodeURIComponent(this.currentProject)}/page/${encodeURIComponent(this.currentPage)}`);
      const content = text || `# ${this.currentPage}\n\n*(Page not found or endpoint not available.)*`;
      this.pageCache[cacheKey] = content;
      this.currentPageContent = content;
      this.loading.page = false;
      this.refreshIcons();
    },

    async loadCurrentCross() {
      if (this.view !== 'crossDetail' || !this.currentCrossSlug) return;
      if (this.crossCache[this.currentCrossSlug] !== undefined) {
        this.currentCrossContent = this.crossCache[this.currentCrossSlug];
        return;
      }
      this.loading.cross = true;
      this.currentCrossContent = '';
      const text = await this.fetchText(`/api/cross/analysis/${encodeURIComponent(this.currentCrossSlug)}`);
      const content = text || '*(Analysis not found.)*';
      this.crossCache[this.currentCrossSlug] = content;
      this.currentCrossContent = content;
      this.loading.cross = false;
      this.refreshIcons();
    },

    async loadSessions() {
      this.loading.sessions = true;
      const text = await this.fetchText('/api/sessions');
      this.sessionsContent = text || '*(Sessions not available.)*';
      this.loading.sessions = false;
      this.refreshIcons();
    },

    async loadResources() {
      this.loading.resources = true;
      const pluginFilter = this.resourcesShowAllPlugins ? '' : '?filter=anja';
      const [skills, plugins, mcp] = await Promise.all([
        this.fetchJson('/api/resources/skills'),
        this.fetchJson(`/api/resources/plugins${pluginFilter}`),
        this.fetchJson('/api/resources/mcp'),
      ]);
      this.resources = {
        skills: (skills && skills.skills) || [],
        plugins: (plugins && plugins.plugins) || [],
        mcp: (mcp && mcp.mcp) || [],
      };
      this.loading.resources = false;
      this.loadPp();
      this.refreshIcons();
    },

    // ===== F-RawUI — Sources & Ingest UI =====

    _sourcesScopeObj() {
      // F-HubKnowledge — hub knowledge layer vs project/workspace.
      if (this.sourcesScope === 'hub') return { scope: 'hub', target: '' };
      return { scope: 'project', target: this.currentProject || '' };
    },

    _sourcesScopeParams() {
      const o = this._sourcesScopeObj();
      return `scope=${o.scope}&target=${encodeURIComponent(o.target)}`;
    },

    sourceFileUrl(topic, filename) {
      return `/api/sources/file?${this._sourcesScopeParams()}&topic=${encodeURIComponent(topic)}&filename=${encodeURIComponent(filename)}`;
    },

    // F-HubKnowledge — apre la Sources view sul knowledge layer dell'hub
    openHubSources() {
      this.view = 'hub-sources';
      this.sourcesScope = 'hub';
      this.workspaceSwitcherOpen = false;
      this.loadProjectSources();
      this.loadSourcesPending();
      this.loadHubWikiPages();
      this.$nextTick(() => this.refreshIcons());
    },

    async loadProjectSources() {
      if (this.sourcesScope !== 'hub' && !this.currentProject) { this.projectSources = { topics: [] }; return; }
      this.loading.sources = true;
      try {
        const data = await this.fetchJson(`/api/sources/list?${this._sourcesScopeParams()}`);
        this.projectSources = data && data.topics ? data : { topics: [] };
        this.loadIngestStatus();
      } catch (e) {
        this.projectSources = { topics: [] };
        this.showToast('error', 'Failed to load sources', e.message);
      } finally {
        this.loading.sources = false;
        this.$nextTick(() => this.refreshIcons());
      }
    },

    async loadSourcesPending() {
      if (this.sourcesScope !== 'hub' && !this.currentProject) { this.sourcesPending = { files: [], last_updated: 0 }; return; }
      try {
        const data = await this.fetchJson(`/api/sources/pending?${this._sourcesScopeParams()}`);
        this.sourcesPending = data && data.files ? data : { files: [], last_updated: 0 };
      } catch (e) {
        this.sourcesPending = { files: [], last_updated: 0 };
      }
    },

    get sourcesStats() {
      const t = (this.projectSources && this.projectSources.topics) || [];
      const total = t.reduce((a, x) => a + (x.count || 0), 0);
      const size = t.reduce((a, x) => a + (x.total_size || 0), 0);
      const pending = (this.sourcesPending && this.sourcesPending.files) || [];
      let s = `(${t.length} topic${t.length === 1 ? '' : 's'}, ${total} file, ${this.formatBytes(size)})`;
      if (pending.length) s += ` · ${pending.length} pending ingest`;
      return s;
    },

    formatBytes(n) {
      if (!n && n !== 0) return '-';
      if (n < 1024) return n + ' B';
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
      if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
      return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
    },

    formatMtime(ts) {
      if (!ts) return '-';
      const d = new Date(ts * 1000);
      const now = new Date();
      const sameDay = d.toDateString() === now.toDateString();
      return sameDay
        ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        : d.toLocaleDateString();
    },

    openAddSourceModal() {
      this.addSourceModal = { open: true, mode: 'url', topic: 'misc', url: '', filename: '', fileInput: null, submitting: false, error: '', maxPages: 25, ingest: false };
      this.$nextTick(() => this.refreshIcons());
    },

    async submitCrawl() {
      this.addSourceModal.error = '';
      const m = this.addSourceModal;
      if (!m.topic) { m.error = 'topic required'; return; }
      if (!m.url || !/^https?:\/\//.test(m.url)) { m.error = 'URL must start with http(s)://'; return; }
      m.submitting = true;
      const sc = this._sourcesScopeObj();
      try {
        const r = await fetch('/api/sources/add-crawl', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope: sc.scope, target: sc.target, topic: m.topic,
                                 url: m.url, max_pages: m.maxPages || 25, ingest: !!m.ingest }),
        });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
        m.open = false;
        this.showToast('info', 'Crawl started', `${m.url} — downloading pages…`);
        this._pollCrawl(0);
      } catch (e) { m.error = e.message || String(e); }
      finally { m.submitting = false; }
    },

    _pollCrawl(n) {
      if (n > 60) return;
      setTimeout(async () => {
        let st = {};
        try { st = await this.fetchJson(`/api/sources/crawl-status?${this._sourcesScopeParams()}`) || {}; } catch {}
        if (st.status === 'done') {
          this.showToast('success', 'Crawl done', `${st.fetched || 0} pages fetched${st.ingested ? ', ' + st.ingested + ' ingested' : ''}`);
          this.loadProjectSources(); this.loadHubWikiPages();
          return;
        }
        if (st.status === 'error') { this.showToast('error', 'Crawl error', st.error || ''); this.loadProjectSources(); return; }
        if (st.fetched) this.loadProjectSources();   // progresso incrementale
        this._pollCrawl(n + 1);
      }, 3000);
    },

    async submitAddSource() {
      this.addSourceModal.error = '';
      const m = this.addSourceModal;
      if (!m.topic) { m.error = 'topic required'; return; }
      m.submitting = true;
      try {
        const sc = this._sourcesScopeObj();
        let body;
        if (m.mode === 'url') {
          if (!m.url || !/^https?:\/\//.test(m.url)) { throw new Error('URL must start with http(s)://'); }
          body = {
            scope: sc.scope, target: sc.target, topic: m.topic,
            mode: 'url', url: m.url,
          };
          if (m.filename) body.filename = m.filename;
        } else {
          if (!m.fileInput) { throw new Error('Select a file'); }
          const buf = await m.fileInput.arrayBuffer();
          const b64 = btoa(String.fromCharCode.apply(null, new Uint8Array(buf)));
          body = {
            scope: sc.scope, target: sc.target, topic: m.topic,
            mode: 'inline', filename: m.fileInput.name, content_b64: b64,
          };
        }
        const r = await fetch('/api/sources/add', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
        this.showToast('success', 'Source added', `${j.filename} (${this.formatBytes(j.size)}) → ${m.topic}/`);
        m.open = false;
        await this.loadProjectSources();
      } catch (e) {
        m.error = e.message || String(e);
      } finally {
        m.submitting = false;
      }
    },

    // F-HubKnowledge — pagine wiki generate (risultato dell'ingest)
    async loadHubWikiPages() {
      try { const d = await this.fetchJson(`/api/wiki/pages?${this._sourcesScopeParams()}`); this.hubWikiPages = (d && d.pages) || []; }
      catch { this.hubWikiPages = []; }
    },

    async openWikiPage(path, title) {
      this.wikiPreview = { open: true, title: title || path, content: '', loading: true };
      try {
        const t = await this.fetchText(`/api/wiki/page?${this._sourcesScopeParams()}&path=${encodeURIComponent(path)}`);
        this.wikiPreview.content = (t || '*(empty)*').replace(/^---\n[\s\S]*?\n---\n+/, '');  // strip YAML frontmatter
      } catch (e) { this.wikiPreview.content = 'Error: ' + e.message; }
      this.wikiPreview.loading = false;
      this.$nextTick(() => this.refreshIcons());
    },

    // F-HubKnowledge — ingest reale: spawna LLM in background → source page nel wiki
    async loadIngestStatus() {
      try { this.ingestStatus = await this.fetchJson(`/api/sources/ingest-status?${this._sourcesScopeParams()}`) || {}; }
      catch { this.ingestStatus = {}; }
    },

    ingestState(topic, filename) {
      const st = this.ingestStatus && this.ingestStatus[`${topic}/${filename}`];
      return st ? st.status : '';
    },

    async ingestSource(topic, filename) {
      const sc = this._sourcesScopeObj();
      try {
        const r = await fetch('/api/sources/ingest-now', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope: sc.scope, target: sc.target, topic, filename }),
        });
        if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `HTTP ${r.status}`); }
        this.ingestStatus[`${topic}/${filename}`] = { status: 'ingesting' };
        this.showToast('info', 'Ingest started', `${topic}/${filename} — synthesizing in background…`);
        this._pollIngest(topic, filename, 0);
      } catch (e) { this.showToast('error', 'Ingest failed', e.message); }
    },

    _pollIngest(topic, filename, n) {
      if (n > 40) return;
      setTimeout(async () => {
        await this.loadIngestStatus();
        const st = this.ingestStatus[`${topic}/${filename}`];
        if (st && st.status === 'done') { this.showToast('success', 'Ingested', `→ wiki/sources/${st.source}`); this.loadHubWikiPages(); return; }
        if (st && st.status === 'error') { this.showToast('error', 'Ingest error', st.error || ''); return; }
        this._pollIngest(topic, filename, n + 1);
      }, 3000);
    },

    async deleteSource(topic, filename) {
      if (!confirm(`Delete ${topic}/${filename}?`)) return;
      try {
        const url = `/api/sources/file?${this._sourcesScopeParams()}&topic=${encodeURIComponent(topic)}&filename=${encodeURIComponent(filename)}`;
        const r = await fetch(url, { method: 'DELETE' });
        if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `HTTP ${r.status}`); }
        this.showToast('success', 'Deleted', `${topic}/${filename}`);
        await this.loadProjectSources();
      } catch (e) {
        this.showToast('error', 'Delete failed', e.message);
      }
    },

    async copySourcePath(topic, filename) {
      // Path locale del file (per usarlo in /anja-ingest)
      const url = this.sourceFileUrl(topic, filename);
      try {
        await navigator.clipboard.writeText(url);
        this.showToast('success', 'URL copied to clipboard');
      } catch {
        this.showToast('info', 'Path', url);
      }
    },

    async openSourcePreview(topic, file) {
      const url = this.sourceFileUrl(topic, file.name);
      const kindMap = {
        pdf: 'pdf',
        png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image', svg: 'image',
        html: 'html', htm: 'html',
        md: 'markdown',
        txt: 'text', rst: 'text', log: 'text', yaml: 'text', yml: 'text',
        json: 'json',
      };
      const kind = kindMap[file.ext] || 'text';
      this.sourcePreview = { open: true, kind, url, content: '', topic, filename: file.name };
      this.$nextTick(() => this.refreshIcons());
      // Per markdown/text/json carica il content inline
      if (kind === 'markdown' || kind === 'text' || kind === 'json') {
        try {
          const r = await fetch(url);
          this.sourcePreview.content = await r.text();
        } catch (e) {
          this.sourcePreview.content = `(error loading: ${e.message})`;
        }
      }
    },

    // ===== Fase P-CLI — Printing Press =====

    async loadPp() {
      this.ppState.loading = true;
      try {
        const r = await this.fetchJson('/api/pp/list');
        this.ppState.items = (r && r.items) || [];
      } catch (e) {
        this.ppState.items = [];
      }
      this.ppState.loading = false;
      this.refreshIcons();
    },

    async openPpDoctor() {
      this.ppDoctorVisible = true;
      this.ppDoctor.busy = true;
      this.ppDoctor.output = '';
      try {
        const r = await this.fetchJson('/api/pp/doctor');
        this.ppDoctor.data = r || {};
      } catch (e) {
        this.ppDoctor.data = { error: String(e) };
      }
      this.ppDoctor.busy = false;
      this.refreshIcons();
    },

    async ppEnsure() {
      this.ppDoctor.busy = true;
      this.ppDoctor.output = 'Installing...';
      try {
        const r = await fetch('/api/pp/ensure', { method: 'POST' });
        const data = await r.json();
        this.ppDoctor.output = JSON.stringify(data, null, 2);
        if (data.ok) {
          await this.openPpDoctor();
        }
      } catch (e) {
        this.ppDoctor.output = 'ERROR: ' + String(e);
      }
      this.ppDoctor.busy = false;
    },

    openPpWizard() {
      this.ppWizardVisible = true;
      this.ppWizard = { name: '', source: '', source_type: 'catalog', busy: false, output: '', error: '' };
    },

    async ppGenerate() {
      if (!this.ppWizard.name) { this.ppWizard.error = 'name required'; return; }
      this.ppWizard.busy = true;
      this.ppWizard.error = '';
      this.ppWizard.output = 'Generating, please wait...';
      try {
        const body = {
          name: this.ppWizard.name,
          source: this.ppWizard.source || 'catalog',
          source_type: this.ppWizard.source_type,
        };
        const r = await fetch('/api/pp/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await r.json();
        this.ppWizard.output = JSON.stringify(data, null, 2);
        if (data.ok) {
          await this.loadPp();
          setTimeout(() => { this.ppWizardVisible = false; }, 2000);
        } else {
          this.ppWizard.error = data.error || 'generate failed';
        }
      } catch (e) {
        this.ppWizard.error = String(e);
      }
      this.ppWizard.busy = false;
    },

    openPpInstall(cli) {
      this.ppInstallVisible = true;
      this.ppInstall = { name: cli.name, scope: 'hub', workspace: '', envText: '', busy: false, error: '', result: '' };
    },

    async ppDoInstall() {
      if (!this.ppInstall.name) return;
      if (this.ppInstall.scope === 'workspace' && !this.ppInstall.workspace) {
        this.ppInstall.error = 'workspace name required';
        return;
      }
      this.ppInstall.busy = true;
      this.ppInstall.error = '';
      // Parse envText "KEY=VALUE\n..."
      const env = {};
      (this.ppInstall.envText || '').split('\n').forEach(line => {
        const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+?)\s*$/);
        if (m) env[m[1]] = m[2];
      });
      try {
        const r = await fetch('/api/pp/install', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: this.ppInstall.name,
            scope: this.ppInstall.scope,
            workspace: this.ppInstall.workspace || null,
            env: env,
          }),
        });
        const data = await r.json();
        this.ppInstall.result = JSON.stringify(data, null, 2);
        if (data.ok) {
          await this.loadPp();
          setTimeout(() => { this.ppInstallVisible = false; }, 1500);
        } else {
          this.ppInstall.error = data.error || 'install failed';
        }
      } catch (e) {
        this.ppInstall.error = String(e);
      }
      this.ppInstall.busy = false;
    },

    async ppUninstall(cli) {
      const scopes = cli.installed_in || [];
      if (scopes.length === 0) {
        if (!confirm(`'${cli.name}' is not installed in any scope. Delete the library from disk?`)) return;
        try {
          const r = await fetch('/api/pp/uninstall', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: cli.name, scope: 'hub', delete_library: true }),
          });
          const data = await r.json();
          if (!data.ok) alert('Error: ' + (data.error || 'unknown'));
        } catch (e) { alert(String(e)); }
        await this.loadPp();
        return;
      }
      // Pick scope: prima trovato
      const first = scopes[0];
      const scope = first === 'hub' ? 'hub' : 'workspace';
      const workspace = first.startsWith('workspace:') ? first.split(':')[1] : null;
      if (!confirm(`Uninstall '${cli.name}' from ${first}?`)) return;
      try {
        const r = await fetch('/api/pp/uninstall', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: cli.name, scope, workspace, delete_library: false }),
        });
        const data = await r.json();
        if (!data.ok) alert('Error: ' + (data.error || 'unknown'));
      } catch (e) { alert(String(e)); }
      await this.loadPp();
    },

    async ppRegenerate(name) {
      if (!confirm(`Regenerate '${name}' from catalog? Overwrites the generated code (env vars are kept).`)) return;
      this.ppState.loading = true;
      try {
        const r = await fetch('/api/pp/generate', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, source: 'catalog', source_type: 'catalog' }),
        });
        const data = await r.json();
        if (!data.ok) alert('Error: ' + (data.error || 'unknown'));
      } catch (e) { alert(String(e)); }
      await this.loadPp();
    },

    openPpDetail(cli) {
      // Opens AGENTS.md / SKILL.md in a new tab via direct file read (TODO)
      window.open(`file://${cli.library_path}/SKILL.md`, '_blank');
    },
    // ===== /Fase P-CLI =====

    // ===== SKILL WIZARD =====
    openSkillWizard() {
      this.skillForm = { name: '', description: '', scope: 'user-global', body: '' };
      this.skillImportForm = { url: '', scope: 'user-global', name: '' };
      this.skillWizardMode = 'manual';
      this.skillWizardError = '';
      this.skillWizardVisible = true;
      this.refreshIcons();
    },

    async importSkill() {
      const f = this.skillImportForm;
      if (!f.url) { this.skillWizardError = 'URL required.'; return; }
      if (!/^https?:\/\//.test(f.url)) { this.skillWizardError = 'URL must be http(s).'; return; }
      if (!f.scope) { this.skillWizardError = 'Scope required.'; return; }
      this.skillSaving = true;
      try {
        const r = await fetch('/api/resources/skills/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(f),
        });
        const data = await r.json();
        if (!r.ok) { this.skillWizardError = data.detail || `HTTP ${r.status}`; return; }
        this.showToast('success', `Skill "${data.name}" imported`, `from ${data.from_url}`);
        this.skillWizardVisible = false;
        await this.loadResources();
      } catch (e) {
        this.skillWizardError = e.message;
      } finally {
        this.skillSaving = false;
      }
    },

    async saveSkill() {
      const f = this.skillForm;
      if (!f.name) { this.skillWizardError = 'Name required.'; return; }
      if (!/^[a-z0-9][a-z0-9_-]*$/.test(f.name)) { this.skillWizardError = 'Name must be kebab-case.'; return; }
      if (!f.description) { this.skillWizardError = 'Description required.'; return; }
      if (!f.scope) { this.skillWizardError = 'Scope required.'; return; }
      this.skillSaving = true;
      try {
        const r = await fetch('/api/resources/skills', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(f),
        });
        const data = await r.json();
        if (!r.ok) { this.skillWizardError = data.detail || `HTTP ${r.status}`; return; }
        this.showToast('success', `Skill "${data.name}" created`, data.path);
        this.skillWizardVisible = false;
        await this.loadResources();
      } catch (e) {
        this.skillWizardError = e.message;
      } finally {
        this.skillSaving = false;
      }
    },

    async viewSkillDetail(scope, name) {
      this.resourceDetailTitle = `${scope} / ${name} / SKILL.md`;
      this.resourceDetailContent = 'Loading…';
      this.resourceDetailVisible = true;
      const text = await this.fetchText(`/api/resources/skill?scope=${encodeURIComponent(scope)}&name=${encodeURIComponent(name)}`);
      this.resourceDetailContent = text || '(unable to load)';
    },

    async deleteSkill(scope, name) {
      if (scope.startsWith('plugin:')) {
        this.showToast('error', 'Read-only', "Plugin skills are edited via the plugin's source code.");
        return;
      }
      if (!confirm(`Delete skill "${name}" (${scope})? The directory will be removed.`)) return;
      try {
        const r = await fetch(`/api/resources/skill?scope=${encodeURIComponent(scope)}&name=${encodeURIComponent(name)}`, { method: 'DELETE' });
        const data = await r.json();
        if (!r.ok) { this.showToast('error', 'Error', data.detail || `HTTP ${r.status}`); return; }
        this.showToast('success', `Skill "${name}" deleted`, scope);
        await this.loadResources();
      } catch (e) {
        this.showToast('error', 'Error', e.message);
      }
    },

    openSkillCopy(from_scope, name) {
      this.copyForm = { name, from_scope, to_scope: '' };
      this.copyError = '';
      this.copyDialogVisible = true;
      this.refreshIcons();
    },

    async executeSkillCopy() {
      if (!this.copyForm.to_scope) { this.copyError = 'Select destination.'; return; }
      this.copySaving = true;
      try {
        const r = await fetch('/api/resources/copy', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind: 'skill', ...this.copyForm }),
        });
        const data = await r.json();
        if (!r.ok) { this.copyError = data.detail || `HTTP ${r.status}`; return; }
        this.showToast('success', `Skill "${data.name}" copied`, `${data.from} → ${data.to}`);
        this.copyDialogVisible = false;
        await this.loadResources();
      } catch (e) {
        this.copyError = e.message;
      } finally {
        this.copySaving = false;
      }
    },

    // ===== MCP WIZARD =====
    openMcpWizard() {
      this.mcpForm = { scope: 'hub', project: '', name: '', command: '', argsText: '', envText: '', type: 'http', url: '', headersText: '' };
      this.mcpAi = { description: '', loading: false, candidates: [], notes: '' };
      this.mcpEditing = false;
      this.mcpWizardMode = 'ai';
      this.mcpWizardError = '';
      this.mcpWizardVisible = true;
      this.refreshIcons();
    },

    async openEditMcp(m) {
      // Fetcha detail completo per popolare il form
      const qs = m.scope === 'hub'
        ? `scope=hub&name=${encodeURIComponent(m.name)}`
        : `project=${encodeURIComponent(m.project || '')}&name=${encodeURIComponent(m.name)}`;
      const data = await this.fetchJson(`/api/resources/mcp/detail?${qs}`);
      if (!data) {
        this.showToast('error', 'Error', 'Unable to load MCP');
        return;
      }
      const cfg = data.config || {};
      const isRemote = !!cfg.url;
      this.mcpForm = {
        scope: m.scope,
        project: m.project || '',
        name: m.name,
        command: cfg.command || '',
        argsText: (cfg.args || []).join('\n'),
        envText: Object.entries(cfg.env || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
        type: cfg.type || 'http',
        url: cfg.url || '',
        headersText: Object.entries(cfg.headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n'),
      };
      this.mcpEditing = true;
      this.mcpWizardMode = isRemote ? 'remote' : 'stdio';
      this.mcpWizardError = '';
      this.mcpWizardVisible = true;
      this.refreshIcons();
    },

    async mcpAiSuggest() {
      if (!this.mcpAi.description.trim()) return;
      this.mcpAi.loading = true;
      this.mcpAi.candidates = [];
      this.mcpAi.notes = '';
      try {
        const r = await fetch('/api/mcp/ai-suggest', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            description: this.mcpAi.description,
            scope: this.mcpForm.scope || 'hub',
          }),
        });
        const data = await r.json();
        if (!r.ok) {
          this.mcpWizardError = data.detail || `HTTP ${r.status}`;
          return;
        }
        this.mcpAi.candidates = data.candidates || [];
        this.mcpAi.notes = data.notes || '';
        this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
      } catch (e) {
        this.mcpWizardError = e.message;
      } finally {
        this.mcpAi.loading = false;
      }
    },

    mcpAiUseCandidate(c) {
      // Pre-popola il form (stdio o remote) e switcha tab
      const cfg = c.config || {};
      this.mcpForm.name = c.name || '';
      if (c.transport === 'http' || c.transport === 'sse' || c.transport === 'websocket' || cfg.url) {
        this.mcpWizardMode = 'remote';
        this.mcpForm.type = c.transport || 'http';
        this.mcpForm.url = cfg.url || '';
        const headers = cfg.headers || {};
        this.mcpForm.headersText = Object.entries(headers).map(([k, v]) => `${k}: ${v}`).join('\n');
      } else {
        this.mcpWizardMode = 'stdio';
        this.mcpForm.command = cfg.command || '';
        this.mcpForm.argsText = (cfg.args || []).join('\n');
        const env = cfg.env || {};
        this.mcpForm.envText = Object.entries(env).map(([k, v]) => `${k}=${v}`).join('\n');
      }
      this.showToast('info', 'Form pre-filled', 'Review and complete env/keys, then click "Add MCP".');
      this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
    },

    async saveMcp() {
      const f = this.mcpForm;
      if (!f.scope) { this.mcpWizardError = 'Scope required.'; return; }
      if (!f.name) { this.mcpWizardError = 'Name required.'; return; }
      if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(f.name)) { this.mcpWizardError = 'Name must be alphanumeric (no spaces).'; return; }

      let body;
      if (this.mcpWizardMode === 'remote') {
        if (!f.url) { this.mcpWizardError = 'URL required for remote MCP.'; return; }
        if (!/^(https?|wss?):\/\//.test(f.url)) { this.mcpWizardError = 'URL must be http(s) or ws(s).'; return; }
        const headers = {};
        for (const line of f.headersText.split('\n')) {
          const t = line.trim();
          if (!t || t.startsWith('#')) continue;
          const colon = t.indexOf(':');
          if (colon < 0) continue;
          headers[t.slice(0, colon).trim()] = t.slice(colon + 1).trim();
        }
        body = { scope: f.scope, name: f.name, type: f.type, url: f.url, headers };
      } else {
        if (!f.command) { this.mcpWizardError = 'Command required for stdio MCP.'; return; }
        const args = f.argsText.split('\n').map(s => s.trim()).filter(Boolean);
        const env = {};
        for (const line of f.envText.split('\n')) {
          const t = line.trim();
          if (!t || t.startsWith('#')) continue;
          const eq = t.indexOf('=');
          if (eq < 0) continue;
          env[t.slice(0, eq).trim()] = t.slice(eq + 1).trim();
        }
        body = { scope: f.scope, name: f.name, command: f.command, args, env };
      }

      // Edit mode: aggiungi project se scope è project (non lo include il body sopra)
      if (this.mcpEditing && f.project) body.project = f.project;

      this.mcpSaving = true;
      try {
        const method = this.mcpEditing ? 'PUT' : 'POST';
        const r = await fetch('/api/resources/mcp', {
          method,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await r.json();
        if (!r.ok) { this.mcpWizardError = data.detail || `HTTP ${r.status}`; return; }
        const tag = data.remote ? `remote (${data.type})` : 'stdio';
        const target = data.scope === 'hub' ? 'hub' : (data.project || data.scope);
        const verb = this.mcpEditing ? 'updated' : 'added';
        this.showToast('success', `MCP "${data.name}" ${verb}`, `${target}/.mcp.json · ${tag}`);
        this.mcpWizardVisible = false;
        this.mcpEditing = false;
        await this.loadResources();
      } catch (e) {
        this.mcpWizardError = e.message;
      } finally {
        this.mcpSaving = false;
      }
    },

    async viewMcpDetail(scope, name, project) {
      const label = scope === 'hub' ? 'hub' : (project || scope);
      this.resourceDetailTitle = `${label} / .mcp.json / ${name}`;
      this.resourceDetailContent = 'Loading…';
      this.resourceDetailVisible = true;
      const qs = scope === 'hub'
        ? `scope=hub&name=${encodeURIComponent(name)}`
        : `project=${encodeURIComponent(project || '')}&name=${encodeURIComponent(name)}`;
      const data = await this.fetchJson(`/api/resources/mcp/detail?${qs}`);
      if (data) {
        this.resourceDetailContent = JSON.stringify(data, null, 2);
      } else {
        this.resourceDetailContent = '(unable to load)';
      }
    },

    openCloneMcp(m) {
      this.cloneMcp.source = { scope: m.scope, name: m.name, project: m.project };
      this.cloneMcp.targetScope = m.scope === 'hub' && this.projects.length > 0 ? `project:${this.projects[0].name}` : 'hub';
      this.cloneMcp.targetName = m.name;
      this.cloneMcp.envOverrideText = '';
      this.cloneMcp.error = '';
      this.cloneMcp.visible = true;
      this.refreshIcons();
    },

    _parseEnvText(text) {
      const out = {};
      for (const raw of (text || '').split('\n')) {
        const t = raw.trim();
        if (!t || t.startsWith('#')) continue;
        const eq = t.indexOf('=');
        if (eq < 0) continue;
        const k = t.slice(0, eq).trim();
        const v = t.slice(eq + 1).trim();
        if (k) out[k] = v;
      }
      return out;
    },

    async confirmCloneMcp() {
      const c = this.cloneMcp;
      if (!c.targetName || !/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(c.targetName)) {
        c.error = 'Target name must be alphanumeric (no spaces).';
        return;
      }
      c.saving = true;
      c.error = '';
      try {
        const r = await fetch('/api/resources/mcp/clone', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source_scope: c.source.scope,
            source_name: c.source.name,
            target_scope: c.targetScope,
            target_name: c.targetName,
            env_override: this._parseEnvText(c.envOverrideText),
          }),
        });
        const data = await r.json();
        if (!r.ok) {
          c.error = data.detail || `HTTP ${r.status}`;
          return;
        }
        const note = data.binary_copied ? `binary copied to ${data.binary_copied}` : 'json entry only';
        this.showToast('success', `MCP "${c.targetName}" duplicated`, note);
        c.visible = false;
        await this.loadResources();
      } catch (e) {
        c.error = e.message;
      } finally {
        c.saving = false;
      }
    },

    async deleteMcp(scope, name, project) {
      const label = scope === 'hub' ? 'hub' : (project || scope);
      if (!confirm(`Remove MCP "${name}" from ${label}/.mcp.json?`)) return;
      try {
        const qs = scope === 'hub'
          ? `scope=hub&name=${encodeURIComponent(name)}`
          : `project=${encodeURIComponent(project || '')}&name=${encodeURIComponent(name)}`;
        const r = await fetch(`/api/resources/mcp?${qs}`, { method: 'DELETE' });
        const data = await r.json();
        if (!r.ok) { this.showToast('error', 'Error', data.detail || `HTTP ${r.status}`); return; }
        this.showToast('success', `MCP "${name}" removed`, label);
        await this.loadResources();
      } catch (e) {
        this.showToast('error', 'Error', e.message);
      }
    },

    async viewPluginDetail(name) {
      this.resourceDetailTitle = `plugin / ${name}`;
      this.resourceDetailContent = 'Loading…';
      this.resourceDetailVisible = true;
      const data = await this.fetchJson(`/api/resources/plugin/detail?name=${encodeURIComponent(name)}`);
      if (data) {
        const lines = [];
        lines.push(`# ${data.name}\n`);
        lines.push(`**Path**: ${data.path}\n`);
        if (data.plugin_json) {
          lines.push('## plugin.json\n');
          lines.push('```json');
          lines.push(JSON.stringify(data.plugin_json, null, 2));
          lines.push('```\n');
        }
        if (data.commands && data.commands.length) {
          lines.push(`## Commands (${data.commands.length})`);
          data.commands.forEach(c => lines.push(`- /${c}`));
          lines.push('');
        }
        if (data.skills && data.skills.length) {
          lines.push(`## Skills (${data.skills.length})`);
          data.skills.forEach(s => lines.push(`- ${s}`));
          lines.push('');
        }
        if (data.readme) {
          lines.push('## README.md\n');
          lines.push(data.readme);
        }
        this.resourceDetailContent = lines.join('\n');
      } else {
        this.resourceDetailContent = '(unable to load)';
      }
    },

    // ===== ACTIONS (POST) =====
    async runAction(action, payload) {
      if (this.actionRunning) {
        this.showToast('error', 'Action already in progress', 'Wait for the current one to finish.');
        return;
      }
      this.actionRunning = action;
      this.showToast('info', `${action} running...`, '');

      try {
        const r = await fetch(`/api/action/${action}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload || {}),
        });
        const result = await r.json();

        if (result.status === 'success') {
          // Per lint, i numeri sono in result.data.by_severity
          let title = `${action} ✓`;
          let body = '';
          if (result.data && result.data.by_severity) {
            const s = result.data.by_severity;
            body = `errors: ${s.error}, warnings: ${s.warning}, suggestions: ${s.suggestion}`;
          } else if (result.stdout) {
            body = result.stdout.split('\n').slice(0, 6).join('\n').slice(0, 400);
          }
          this.showToast('success', title, body);

          // Refresh registry/health post-action
          if (action === 'sync') {
            await this.loadRegistry();
          }
          if (action === 'lint-hub') {
            await this.loadHealth();
          }
          if (action === 'aggregate-sessions' && this.view === 'sessions') {
            await this.loadSessions();
          }
        } else {
          this.showToast('error', `${action} ✗`, result.stderr || result.message || `exit ${result.exit}`);
        }
      } catch (e) {
        this.showToast('error', `${action} ✗`, String(e));
      } finally {
        this.actionRunning = null;
        this.refreshIcons();
      }
    },

    showToast(type, title, body = '') {
      const id = ++this._toastSeq;
      this.toasts.push({ id, type, title, body });
      this.refreshIcons();
      const ttl = type === 'error' ? 8000 : 4000;
      setTimeout(() => {
        this.toasts = this.toasts.filter(t => t.id !== id);
      }, ttl);
    },

    // ===== NAV METHODS =====
    goHubHome() {
      this.view = 'hub-home';
      this.loadHubRecentFiles();  // Fase 22.9 — auto-load Anja's recent files
      this.loadAnjaStatus();       // F22.9.3 — auto-load Anja status card
      this.refreshIcons();
    },

    newConversation() {
      this.view = 'chat';
      this.currentConversation = null;
      this.currentConvId = null;
      this.currentAgentChatName = null;
      this.messages = [];
      this._lastChatScope = 'hub';
      this.chatUsage = { tokens: 0, ctx: 0, lastIn: 0, lastOut: 0, cacheRead: 0 };  // Fase 7t reset
      this._applyHubDefaultsToPicker();   // nuova chat = riparte dai Hub defaults
      if (!this.wsConnected) this.connectWs();
      this.refreshIcons();
    },

    async _applyHubDefaultsToPicker() {
      // Il picker della chat parte dai Hub defaults (Settings → Providers),
      // non dall'hardcoded claude/sonnet. Le conversation esistenti mantengono
      // il loro provider/model (selectConversation li ripristina dopo).
      try {
        if (!this._hubDefaultsLoaded) {
          const data = await this.fetchJson('/api/settings/defaults');
          if (data) {
            this.hubDefaults.provider = data.default_provider || 'claude';
            this.hubDefaults.model = data.default_model || 'sonnet';
            this.hubDefaults.effort = data.default_effort || 'off';
          }
          this._hubDefaultsLoaded = true;
        }
        this.selectedProvider = this.hubDefaults.provider || 'claude';
        this.selectedModel = this.hubDefaults.model || 'sonnet';
        this.selectedEffort = (this.hubDefaults.effort && this.hubDefaults.effort !== 'off')
          ? this.hubDefaults.effort : '';
        await this._ensureModelsFor(this.selectedProvider);
      } catch (e) { /* restano i default hardcoded */ }
    },

    async selectConversation(id) {
      this.currentConversation = id;
      this.currentConvId = id;
      this.messages = [];
      this.chatUsage = { tokens: 0, ctx: 0, lastIn: 0, lastOut: 0, cacheRead: 0 };  // Fase 7t reset
      this.refreshIcons();

      // Fetch contenuto reale
      const data = await this.fetchJson(`/api/conversations/${encodeURIComponent(id)}`);
      if (!data) {
        this.showToast('error', 'Conversation not found', '');
        return;
      }

      // Determina view in base allo scope persistito
      const scope = data.scope || 'hub';
      if (scope.startsWith('project:')) {
        const projName = scope.split(':', 2)[1];
        this.currentAgentChatName = null;
        if (this.workspaceScope === scope) {
          // Dentro il workspace: chat moderna (lo scope arriva da workspaceScope)
          this.view = 'chat';
        } else {
          // Dal contesto hub: viewer del progetto sulla tab Chat
          this.view = 'viewer';
          this.currentProject = projName;
          this.currentTab = 'Chat';
          this.expandedProjects[projName] = true;
        }
        this._lastChatScope = scope;
      } else if (scope.startsWith('agent:')) {
        this.view = 'chat';
        this.currentAgentChatName = scope.split(':', 2)[1];
        this._lastChatScope = scope;
      } else {
        this.view = 'chat';
        this.currentAgentChatName = null;
        this._lastChatScope = 'hub';
      }

      this.messages = data.messages || [];
      // Ripristina provider/model/effort della conversation se presenti
      if (data.provider) {
        this.selectedProvider = data.provider;
        await this._ensureModelsFor(data.provider);
      }
      if (data.model) this.selectedModel = data.model;
      if (data.effort !== undefined) this.selectedEffort = data.effort;
      if (!this.wsConnected) this.connectWs();
      this.refreshIcons();

      // F-Notify-5: se la conv ha stream attivo in background, chiedi resume per
      // replay buffer + tail nuovi chunk.
      if (this.chatActiveStreams.has(id) && this.ws && this.wsConnected) {
        try {
          this.ws.send(JSON.stringify({ action: 'resume', conv_id: id, since_seq: 0 }));
          this.chatStreaming = true;
        } catch (e) { console.warn('[chat] resume send:', e); }
      }
    },

    // F-MultiChatView ---------------------------------------------------------
    toggleSplit() {
      this.splitView = !this.splitView;
      if (!this.splitView) { this.secondConvId = null; this.secondMessages = []; }
      this.$nextTick(() => this.refreshIcons());
    },
    closeSplit() {
      this.splitView = false; this.secondConvId = null; this.secondMessages = [];
      this.$nextTick(() => this.refreshIcons());
    },
    async openSecond(id) {
      if (!id) { this.secondConvId = null; this.secondMessages = []; return; }
      this.secondConvId = id;
      this.secondMessages = [];
      const data = await this.fetchJson(`/api/conversations/${encodeURIComponent(id)}`);
      this.secondMessages = (data && data.messages) || [];
      if (!this.wsConnected) this.connectWs();
      // se la conv ha uno stream attivo in background, resume per il tail live
      if (this.chatActiveStreams.has(id) && this.ws && this.wsConnected) {
        try { this.ws.send(JSON.stringify({ action: 'resume', conv_id: id, since_seq: 0 })); } catch (e) {}
      }
      this.$nextTick(() => this.refreshIcons());
    },
    sendSecond() {
      const txt = (this.secondInput || '').trim();
      if (!txt || !this.secondConvId) return;
      if (this.secondStreaming) { this.showToast('info', 'In progress', 'Wait for the 2nd pane response'); return; }
      if (!this.ws || !this.wsConnected) { this.connectWs(); this.secondInput = txt; setTimeout(() => this.sendSecond(), 500); return; }
      this.secondMessages.push({ role: 'user', content: txt });
      this.secondMessages.push({ role: 'claude', content: '' });  // bubble streaming
      this.secondInput = '';
      this.secondStreaming = true;
      const payload = {
        message: txt,
        conversation_id: this.secondConvId,
        model: this.selectedModel,
        provider: this.selectedProvider,
        scope: 'hub',
      };
      if (this.sdkSessionByConv[this.secondConvId]) payload.sdk_session_id = this.sdkSessionByConv[this.secondConvId];
      this.ws.send(JSON.stringify(payload));
      this.refreshIcons();
    },

    selectWorkspace(name) {
      this.view = 'viewer';
      this.currentProject = name;
      this.currentTab = 'Index';
      this.currentPage = 'index';
      this.expandedProjects[name] = true;
      this.refreshIcons();
      this.loadCurrentPage();
    },

    toggleProject(name) {
      this.expandedProjects[name] = !this.expandedProjects[name];
      if (this.expandedProjects[name] && this.currentProject !== name) {
        this.selectWorkspace(name);
      }
      this.refreshIcons();
    },

    openProjectTab(name, tab) {
      this.view = 'viewer';
      this.currentProject = name;
      this.sourcesScope = 'project';   // reset scope quando si apre un progetto/workspace
      this.expandedProjects[name] = true;
      this.currentTab = tab;
      this.currentPage = tab.toLowerCase().replace(/ /g, '-');
      this.refreshIcons();
      if (tab === 'Sources') {
        this.loadProjectSources();
        this.loadSourcesPending();
      } else {
        this.loadCurrentPage();
      }
    },

    selectTab(tab) {
      this.currentTab = tab;
      this.currentPage = tab.toLowerCase().replace(/ /g, '-');
      // Quando entriamo nel tab Sessions, carichiamo le CC sessions
      if (tab === 'Sessions') {
        this.loadCcSessions(this.currentProject);
        this.ccSessionDetail = null;
      }
      // F-RawUI — load raw sources della view Sources
      if (tab === 'Sources') {
        this.loadProjectSources();
        this.loadSourcesPending();
      }
      // Se entriamo nel tab Chat, gestiamo lo scope chat + carichiamo project context + project chats
      if (tab === 'Chat') {
        const newScope = `project:${this.currentProject}`;
        if (this._lastChatScope !== newScope) {
          this.messages = [];
          this.currentConvId = null;
          this.currentConversation = null;
          this._lastChatScope = newScope;
        }
        if (!this.wsConnected) this.connectWs();
        this.loadProjectContext(this.currentProject);
        this.projectConversationsProject = null;  // force reload
        this.loadProjectConversations(this.currentProject);
        this.fileTreeProject = null;  // force reload
        this.loadFileTree(this.currentProject);
      }
    },

    async loadProjectContext(projectName) {
      if (!projectName) return;
      if (this.projectContextProject === projectName) return;  // cache
      this.projectContextProject = projectName;
      const data = await this.fetchJson(`/api/project/${encodeURIComponent(projectName)}/context`);
      if (data) {
        this.projectContext = {
          log_entries: data.log_entries || [],
          sessions: data.sessions || [],
          conversations: data.conversations || 0,
        };
      }
      this.refreshIcons();
    },

    async loadCcSessions(projectName) {
      if (!projectName) return;
      if (this.ccSessionsProject === projectName) return;  // cache
      this.ccSessionsProject = projectName;
      const data = await this.fetchJson(`/api/project/${encodeURIComponent(projectName)}/cc-sessions?limit=30`);
      this.ccSessions = (data && data.sessions) || [];
      this.refreshIcons();
    },

    async openCcSession(sessionId) {
      this.ccSessionLoading = true;
      this.ccSessionDetail = null;
      this.refreshIcons();
      const data = await this.fetchJson(`/api/project/${encodeURIComponent(this.currentProject)}/cc-sessions/${encodeURIComponent(sessionId)}`);
      this.ccSessionDetail = data;
      this.ccSessionLoading = false;
      this.refreshIcons();
    },

    closeCcSession() {
      this.ccSessionDetail = null;
      this.refreshIcons();
    },

    formatCcDate(iso) {
      if (!iso) return '';
      try {
        const d = new Date(iso);
        return d.toISOString().slice(0, 16).replace('T', ' ');
      } catch (e) { return iso.slice(0, 16); }
    },

    selectCrossAnalysis(slug) {
      this.view = 'crossDetail';
      this.currentCrossSlug = slug;
      this.refreshIcons();
      this.loadCurrentCross();
    },

    openResource(tab) {
      this.view = 'resources';
      this.currentResourceTab = tab;
      this.refreshIcons();
    },

    selectRoutine(name) {
      this.view = 'routineDetail';
      this.currentRoutineName = name;
      this.loadRoutineDetail(name);
      this.refreshIcons();
    },

    addProject() {
      // Creazione workspace = Marketplace (blueprint gallery); i progetti dev
      // esterni si registrano con /anja-register da Claude Code.
      this.openMarketplace();
    },

    addRoutineStub() {
      this.openRoutineWizard();
    },

    // ===== ROUTINE WIZARD =====
    openRoutineWizard() {
      this.wizardForm = {
        name: '',
        description: '',
        scope: 'hub',
        schedule: '0 8 * * *',
        provider: 'claude',
        model: 'sonnet',
        effort: 'off',
        prompt: '',
        timeout_sec: 300,
        tags: '',
        enabled: true,
        selectedTools: {},
        output: [],
      };
      this.wizardError = '';
      this.wizardVisible = true;
      this.loadWizardTools();
      // Carica subito la lista modelli per il provider iniziale
      this._ensureModelsFor(this.wizardForm.provider);
      this.refreshIcons();
    },

    async loadWizardTools() {
      this.wizardToolsLoading = true;
      try {
        const data = await this.fetchJson(`/api/wizard/tools?scope=${encodeURIComponent(this.wizardForm.scope)}`);
        this.wizardTools = data || { builtin: [], skills: [], plugins: [], mcp: [] };
      } finally {
        this.wizardToolsLoading = false;
        this.refreshIcons();
      }
    },

    closeRoutineWizard() {
      this.wizardVisible = false;
      this.wizardError = '';
    },

    addOutputAction(type) {
      const defaults = {
        file:        { type: 'file', path: 'routines/output.txt', mode: 'overwrite', template: '' },
        email:       {
          type: 'email',
          to: '',
          subject: '[anja] {date}',
          smtp: {
            host: '',
            port: 587,
            user: '',
            password: '{{SMTP_PASS}}',
            from: '',
            tls: true,
          },
        },
        slack:       { type: 'slack', webhook_url: '{{SLACK_WEBHOOK_URL}}' },
        google_chat: { type: 'google_chat', webhook_url: '{{GCHAT_WEBHOOK_URL}}' },
        wiki_ingest: { type: 'wiki_ingest', target_project: '', raw_subdir: 'routines', auto_ingest: false },
        wiki_page_hub: { type: 'wiki_page_hub', slug: '' },
      };
      this.wizardForm.output.push(defaults[type] || { type });
      this.refreshIcons();
    },

    removeOutputAction(idx) {
      this.wizardForm.output.splice(idx, 1);
    },

    validateWizard() {
      const f = this.wizardForm;
      if (!f.name) return 'Name required.';
      if (!/^[a-z0-9][a-z0-9_-]*$/.test(f.name)) return 'Name must be kebab-case (lowercase, digits, dash, underscore).';
      if (!f.scope) return 'Scope required.';
      if (f.scope !== 'hub' && !f.scope.startsWith('project:')) return 'Invalid scope.';
      if (!f.schedule) return 'Schedule required.';
      if (!/^\s*\S+\s+\S+\s+\S+\s+\S+\s+\S+\s*$/.test(f.schedule)) return 'Schedule must be cron 5-field.';
      if (!f.prompt.trim()) return 'Prompt required.';
      // Check output
      for (let i = 0; i < f.output.length; i++) {
        const o = f.output[i];
        if (!o.type) return `Output #${i + 1}: type missing.`;
        if (o.type === 'email') {
          if (!o.to) return `Output #${i + 1} (email): "to" missing.`;
          // SMTP: se almeno uno dei campi è valorizzato, host+user+password sono richiesti
          const s = o.smtp || {};
          const anySet = (s.host || s.user || (s.password && s.password !== '{{SMTP_PASS}}'));
          if (anySet) {
            if (!s.host) return `Output #${i + 1} (email): smtp.host missing.`;
            if (!s.user) return `Output #${i + 1} (email): smtp.user missing.`;
            if (!s.password) return `Output #${i + 1} (email): smtp.password missing.`;
          }
        }
        if (o.type === 'wiki_ingest' && !o.target_project) return `Output #${i + 1} (wiki_ingest): target_project missing.`;
        if (o.type === 'file' && !o.path) return `Output #${i + 1} (file): path missing.`;
      }
      return null;
    },

    async saveWizard() {
      const err = this.validateWizard();
      if (err) {
        this.wizardError = err;
        return;
      }
      this.wizardError = '';
      this.wizardSaving = true;

      const f = this.wizardForm;
      const tools = Object.entries(f.selectedTools).filter(([, v]) => v).map(([k]) => k);
      const tags = f.tags.split(',').map(s => s.trim()).filter(Boolean);

      const body = {
        name: f.name,
        scope: f.scope,
        schedule: f.schedule,
        prompt: f.prompt,
        enabled: !!f.enabled,
      };
      if (f.description) body.description = f.description;
      if (f.provider && f.provider !== 'claude') body.provider = f.provider;
      if (f.model) body.model = f.model;
      if (f.effort && f.effort !== 'off' && (!f.provider || f.provider === 'claude')) body.effort = f.effort;
      if (tools.length) body.tools = tools;
      if (f.output && f.output.length) {
        body.output = f.output.map(o => {
          // Deep cleanup: rimuovi keys con valore "", null, undefined; ricorri su nested dict
          const cleanObj = (obj) => {
            if (obj === null || obj === undefined) return undefined;
            if (typeof obj !== 'object' || Array.isArray(obj)) return obj;
            const c = {};
            for (const [k, v] of Object.entries(obj)) {
              if (v === '' || v === null || v === undefined) continue;
              if (typeof v === 'object' && !Array.isArray(v)) {
                const nested = cleanObj(v);
                if (nested && Object.keys(nested).length > 0) c[k] = nested;
              } else {
                c[k] = v;
              }
            }
            return c;
          };
          return cleanObj(o);
        });
      }
      if (f.timeout_sec && f.timeout_sec !== 300) body.timeout_sec = parseInt(f.timeout_sec, 10);
      if (tags.length) body.tags = tags;

      try {
        const r = await fetch('/api/routines', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await r.json();
        if (!r.ok) {
          this.wizardError = data.detail || `HTTP ${r.status}`;
          this.wizardSaving = false;
          return;
        }
        this.showToast('success', `Routine "${data.name}" created`, 'Opened in detail view.');
        this.wizardVisible = false;
        this.wizardSaving = false;
        await this.loadRoutines();
        this.selectRoutine(data.name);
      } catch (e) {
        this.wizardError = e.message;
        this.wizardSaving = false;
      }
    },

    // ===== ROUTINES =====
    async loadRoutines() {
      const data = await this.fetchJson('/api/routines');
      if (data && Array.isArray(data.routines)) {
        this.routines = data.routines;
      } else {
        this.routines = [];
      }
      this.refreshIcons();
    },

    async loadRoutineDetail(name) {
      this.routineDetailLoading = true;
      this.routineDetail = null;
      try {
        const data = await this.fetchJson(`/api/routines/${encodeURIComponent(name)}`);
        if (data) this.routineDetail = data;
      } finally {
        this.routineDetailLoading = false;
        this.refreshIcons();
      }
      this.startRoutineStatusPolling(name);
    },

    async fetchRoutineStatus(name) {
      try {
        const data = await this.fetchJson(`/api/routines/${encodeURIComponent(name)}/status`);
        if (!data) return;
        const wasRunning = this.routineStatus.running;
        this.routineStatus.running = !!data.running;
        if (data.running) {
          this.routineStatus.pid = data.pid;
          this.routineStatus.started_at = data.started_at;
          this.routineStatus.duration_sec = data.duration_sec;
          this.routineStatus.dry_run = data.dry_run;
          this.routineStatus.tail = data.tail || '';
        } else if (wasRunning) {
          // Just finished — refresh detail (last_run, last_duration)
          this.loadRoutineDetail(name);
        }
      } catch (e) {}
    },

    startRoutineStatusPolling(name) {
      this.stopRoutineStatusPolling();
      this.fetchRoutineStatus(name);
      this._routineStatusInterval = setInterval(() => {
        if (this.currentRoutineName === name && this.view === 'routineDetail') {
          this.fetchRoutineStatus(name);
        } else {
          this.stopRoutineStatusPolling();
        }
      }, 2000);
    },

    stopRoutineStatusPolling() {
      if (this._routineStatusInterval) {
        clearInterval(this._routineStatusInterval);
        this._routineStatusInterval = null;
      }
    },

    async triggerRoutine(name, dryRun) {
      this.routineTriggering = true;
      try {
        const r = await fetch(`/api/routines/${encodeURIComponent(name)}/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dry_run: !!dryRun }),
        });
        const data = await r.json();
        if (data.status === 'started') {
          this.showToast('success', `Routine "${name}" started`, `pid ${data.pid}${dryRun ? ' · dry-run' : ''}. Refresh in 5s…`);
          setTimeout(() => {
            if (this.currentRoutineName === name) this.loadRoutineDetail(name);
            this.loadRoutines();
          }, 5000);
        } else {
          this.showToast('error', `Trigger failed`, data.error || 'unknown');
        }
      } catch (e) {
        this.showToast('error', `Trigger failed`, e.message);
      } finally {
        this.routineTriggering = false;
      }
    },

    async toggleRoutine(name, enabled) {
      try {
        const r = await fetch(`/api/routines/${encodeURIComponent(name)}/toggle`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled }),
        });
        const data = await r.json();
        if (data.status === 'ok') {
          this.showToast('success', `Routine "${name}"`, enabled ? 'enabled' : 'disabled');
          await this.loadRoutines();
          if (this.currentRoutineName === name) this.loadRoutineDetail(name);
        }
      } catch (e) {
        this.showToast('error', 'Toggle failed', e.message);
      }
    },

    async openRunLog(routineName, filename) {
      this.runLogVisible = true;
      this.runLogFile = filename;
      this.runLogContent = 'Loading…';
      const text = await this.fetchText(`/api/routines/${encodeURIComponent(routineName)}/runs/${encodeURIComponent(filename)}`);
      this.runLogContent = text || '(unable to load the log)';
    },

    formatActionConfig(out) {
      if (!out || typeof out !== 'object') return '';
      const cfg = { ...out };
      delete cfg.type;
      const parts = [];
      for (const [k, v] of Object.entries(cfg)) {
        if (v === null || v === undefined) continue;
        const s = typeof v === 'string' ? v : JSON.stringify(v);
        parts.push(`${k}=${s.length > 80 ? s.slice(0, 77) + '…' : s}`);
      }
      return parts.join('  ');
    },

    // ===== AGENTS (M-PA 1+2) =====

    /** Apre chat con questo agent (M-PA 2). Setta scope=agent:<name>, pulisce messages, connect WS. */
    openAgentChat(name, agentConfig = null) {
      this.view = 'chat';
      this.currentAgentChatName = name;
      this.currentProject = null;
      this.currentTab = null;
      this.currentConversation = null;
      this.messages = [];
      this._lastChatScope = `agent:${name}`;
      // Override model con quello dell'agent se disponibile
      if (agentConfig && agentConfig.default_model) {
        this.selectedModel = agentConfig.default_model;
      }
      if (agentConfig && agentConfig.default_effort && agentConfig.default_effort !== 'off') {
        this.selectedEffort = agentConfig.default_effort;
      }
      if (!this.wsConnected) this.connectWs();
      this.refreshIcons();
    },

    async loadAgents() {
      const data = await this.fetchJson('/api/agents');
      this.agents = (data && data.agents) || [];
      this.refreshIcons();
    },

    async loadProjectAgents() {
      if (!this.isProjectScope) {
        this.projectAgents = [];
        return;
      }
      const name = this.currentProjectScopeName;
      const data = await this.fetchJson(`/api/agents?project=${encodeURIComponent(name)}`);
      this.projectAgents = (data && data.agents) || [];
      this.refreshIcons();
    },

    openProjectAgentChat(agentName) {
      // Apri chat scope agent:<name> con il project-scope agent
      // (server.py routing risolverà tramite project location)
      this.showToast('info', `Opening chat with agent ${agentName}`,
        'Project-scope agent chat (MVP: not fully implemented yet, agent lives in .anjawiki/agents/)');
      // Future: this.currentAgentChatName = agentName; this.view = 'chat'; ecc.
    },

    openProjectAgentWizard() {
      this.showToast('info', 'Project agent wizard',
        `For now create the agent manually: mkdir -p ${this.currentProjectScopeName}/.anjawiki/agents/<name>/ with config.json + SOUL.md + AGENTS.md. Wizard UI coming soon.`);
    },

    async openAgentDetail(name) {
      this.view = 'agentDetail';
      this.currentAgentName = name;
      this.currentAgentTab = 'overview';
      this.agentDetail = null;
      this.agentFileContent = '';
      this.agentSessions = [];
      this.agentSessionDetail = null;
      this.agentChatHistory = [];
      this.expandedAgents[name] = true;
      // Scope-aware: in project mode usa endpoint con project query param
      const url = this.isProjectScope
        ? `/api/agents/${encodeURIComponent(name)}?project=${encodeURIComponent(this.currentProject)}`
        : `/api/agents/${encodeURIComponent(name)}`;
      const data = await this.fetchJson(url);
      if (data) {
        data._scope = this.isProjectScope ? 'project:' + this.currentProject : 'hub';
        this.agentDetail = data;
      }
      this.refreshIcons();
    },

    toggleAgent(name) {
      this.expandedAgents[name] = !this.expandedAgents[name];
      if (this.expandedAgents[name] && this.currentAgentName !== name) {
        this.openAgentDetail(name);
      }
      this.refreshIcons();
    },

    async openAgentTab(name, tab) {
      // Se non sono nel detail dell'agent, apro
      if (this.currentAgentName !== name) {
        this.currentAgentName = name;
        const data = await this.fetchJson(`/api/agents/${encodeURIComponent(name)}`);
        if (data) this.agentDetail = data;
      }
      this.view = 'agentDetail';
      this.currentAgentTab = tab;
      this.expandedAgents[name] = true;

      // Lazy load del contenuto in base al tab
      if (tab === 'AGENTS.md' || tab === 'SOUL.md' || tab === 'TOOLS.md' || tab === 'CLAUDE.md') {
        const proj = this.currentProject ? `&project=${encodeURIComponent(this.currentProject)}` : '';
        const text = await this.fetchText(`/api/agents/${encodeURIComponent(name)}/file?filename=${encodeURIComponent(tab)}${proj}`);
        this.agentFileContent = text || '(unable to load)';
      } else if (tab === 'sessions') {
        const q = this.currentProject ? `?project=${encodeURIComponent(this.currentProject)}` : '';
        const data = await this.fetchJson(`/api/agents/${encodeURIComponent(name)}/sessions${q}`);
        this.agentSessions = (data && data.sessions) || [];
      } else if (tab === 'chats') {
        const data = await this.fetchJson(`/api/conversations?scope=${encodeURIComponent('agent:' + name)}`);
        this.agentChatHistory = (data && data.conversations) || [];
      }
      this.refreshIcons();
    },

    async openAgentSessionDetail(name, sessionId) {
      const text = await this.fetchText(`/api/agents/${encodeURIComponent(name)}/session/${encodeURIComponent(sessionId)}`);
      this.agentSessionDetail = { id: sessionId, content: text || '(not found)' };
      this.refreshIcons();
    },

    openAgentWizard() {
      // Pre-fill project scope se siamo nel context di un project (Fase 13+)
      const proj = this.currentChatScope === 'project' && this.currentProject ? this.currentProject : '';
      this.agentForm = { name: '', role: '', domain: '', provider: 'claude', model: 'sonnet', effort: 'off', project: proj };
      this.agentAi = { description: '', loading: false, suggestion: null };
      this.agentWizardMode = 'ai';
      this.agentWizardError = '';
      this.agentWizardVisible = true;
      this._ensureModelsFor('claude');
      this.refreshIcons();
    },

    async onAgentProviderChange() {
      await this._ensureModelsFor(this.agentForm.provider);
      const list = (this.modelsCatalog[this.agentForm.provider] || []).filter(x => !/(image|video|tts|whisper|audio|embedding|moderation|dall-e)/i.test(x));
      if (list.length && !list.includes(this.agentForm.model)) {
        this.agentForm.model = list[0];
      }
      if (this.agentForm.provider !== 'claude') {
        this.agentForm.effort = 'off';
      }
    },

    async agentAiSuggest() {
      if (!this.agentAi.description.trim()) return;
      this.agentAi.loading = true;
      this.agentAi.suggestion = null;
      try {
        const r = await fetch('/api/agents/ai-suggest', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ description: this.agentAi.description }),
        });
        const data = await r.json();
        if (!r.ok) {
          this.agentWizardError = data.detail || `HTTP ${r.status}`;
          return;
        }
        this.agentAi.suggestion = data;
      } catch (e) {
        this.agentWizardError = e.message;
      } finally {
        this.agentAi.loading = false;
      }
    },

    agentAiApply() {
      const s = this.agentAi.suggestion || {};
      if (s.name) this.agentForm.name = s.name;
      if (s.role) this.agentForm.role = s.role;
      if (s.domain) this.agentForm.domain = s.domain;
      if (s.provider) this.agentForm.provider = s.provider;
      if (s.model) this.agentForm.model = s.model;
      if (s.effort) this.agentForm.effort = s.effort;
      this.agentWizardMode = 'manual';
      this._ensureModelsFor(this.agentForm.provider);
      this.showToast('info', 'Applied', 'Review and save in Manual tab.');
      this.refreshIcons();
    },

    async saveAgent() {
      const f = this.agentForm;
      if (!f.name) { this.agentWizardError = 'Name required.'; return; }
      if (!/^[a-z0-9][a-z0-9_-]*$/.test(f.name)) { this.agentWizardError = 'Name must be kebab-case.'; return; }
      if (!f.role) { this.agentWizardError = 'Role required.'; return; }
      this.agentWizardSaving = true;
      try {
        const r = await fetch('/api/agents', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(f),
        });
        const data = await r.json();
        if (!r.ok) {
          this.agentWizardError = data.detail || `HTTP ${r.status}`;
          this.agentWizardSaving = false;
          return;
        }
        this.showToast('success', `Agent "${data.name}" created`, '');
        this.agentWizardVisible = false;
        await this.loadAgents();
        this.openAgentDetail(data.name);
      } catch (e) {
        this.agentWizardError = e.message;
      } finally {
        this.agentWizardSaving = false;
      }
    },

    // Fase 18.B — Agent clone
    async loadCloneableAgents() {
      try {
        // Hub agents
        const hubRes = await this.fetchJson('/api/agents');
        const cands = (hubRes.agents || []).map(a => ({
          key: 'hub:' + a.name,
          label: `[hub] ${a.name}${a.role ? ' — ' + a.role.substring(0, 40) : ''}`,
          source_name: a.name, source_project: '',
        }));
        // Workspace agents (per ogni workspace)
        for (const ws of (this.projects || [])) {
          try {
            const wsRes = await this.fetchJson(`/api/agents?project=${encodeURIComponent(ws.name)}`);
            for (const a of (wsRes.agents || [])) {
              cands.push({
                key: `project:${ws.name}:${a.name}`,
                label: `[${ws.name}] ${a.name}${a.role ? ' — ' + a.role.substring(0, 40) : ''}`,
                source_name: a.name, source_project: ws.name,
              });
            }
          } catch (e) { /* skip */ }
        }
        this.agentCloneForm.candidates = cands;
      } catch (e) {
        console.error('loadCloneableAgents failed', e);
      }
    },

    async cloneAgent() {
      const f = this.agentCloneForm;
      if (!f.source_key || !f.target_name) {
        this.agentWizardError = 'Select source agent and specify target_name';
        return;
      }
      const cand = (f.candidates || []).find(c => c.key === f.source_key);
      if (!cand) {
        this.agentWizardError = 'Source agent not found';
        return;
      }
      this.agentWizardSaving = true;
      this.agentWizardError = '';
      try {
        const r = await fetch('/api/agents/clone', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source_name: cand.source_name,
            source_project: cand.source_project || null,
            target_name: f.target_name,
            target_project: f.target_project || null,
            include_config: f.include_config,
          }),
        });
        if (!r.ok) throw new Error((await r.text()).substring(0, 300));
        const data = await r.json();
        this.showToast('success', `Cloned → ${data.target_name}`, '');
        this.agentWizardVisible = false;
        await this.loadAgents();
      } catch (e) {
        this.agentWizardError = e.message;
      } finally {
        this.agentWizardSaving = false;
      }
    },

    async deleteAgent(name) {
      if (!confirm(`Delete agent "${name}"? The whole directory will be removed.`)) return;
      try {
        const r = await fetch(`/api/agents/${encodeURIComponent(name)}`, { method: 'DELETE' });
        const data = await r.json();
        if (!r.ok) { this.showToast('error', 'Delete failed', data.detail || `HTTP ${r.status}`); return; }
        this.showToast('success', `Agent "${name}" deleted`, '');
        if (this.currentAgentName === name) {
          this.view = 'agents';
          this.currentAgentName = null;
        }
        await this.loadAgents();
      } catch (e) {
        this.showToast('error', 'Delete failed', e.message);
      }
    },

    // ===== SETTINGS — Provider API keys (Fase 7e) =====
    async openSettings() {
      this.view = 'settings';
      // Fase 13: in project scope auto-seleziona tab Project
      if (this.isProjectScope) {
        this.settingsTab = 'project';
        await this.loadProjectPrefs();
        await this.loadAutoIngest();
      } else if (this.settingsTab === 'project') {
        // Switch hub → providers se tab era project
        this.settingsTab = 'providers';
      }
      await this.loadHubDefaults();
      await this.loadSettings();
      await this.loadCustomSecrets();
      await this.loadTelegramStatus();
      await this.loadOllamaConfig();
      await this.loadOpenaiOauthStatus();
      await this.loadClaudeOauthStatus();
      // Rebuild unified picker dopo che i provider state sono aggiornati
      await this.buildUnifiedModels();
      this.refreshIcons();
    },

    // ===== Fase 18.A — Goals =====

    async openGoals() {
      this.view = 'goals';
      await this.loadGoals();
      await this.loadGoalSuggestions();
    },

    async loadGoalsMatrix() {
      try {
        const data = await this.fetchJson('/api/goals/matrix');
        this.goalsMatrix = data;
      } catch (e) {
        console.error('loadGoalsMatrix failed', e);
      }
    },

    async loadGoalSuggestions() {
      try {
        const data = await this.fetchJson('/api/goals/suggestions?status=pending');
        this.goalsState.suggestions = data.suggestions || [];
      } catch (e) {
        console.error('loadGoalSuggestions failed', e);
        this.goalsState.suggestions = [];
      }
    },

    async resolveGoalSuggestion(sugId, action) {
      try {
        const r = await fetch(`/api/goals/suggestions/${sugId}/resolve`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action }),
        });
        if (!r.ok) throw new Error(await r.text());
        this.showToast('success', `Suggestion ${action}d`);
        await this.loadGoalSuggestions();
        if (action === 'approve') await this.loadGoals();
      } catch (e) {
        this.showToast('error', 'Resolve failed', e.message);
      }
    },

    _currentGoalsScope() {
      // null = tutti, oppure 'hub' | 'workspace:<name>'
      if (this.isHubScope) return 'hub';
      if (this.isProjectScope && this.currentProject) return 'workspace:' + this.currentProject;
      return null;
    },

    _splitScope(scope) {
      // 'hub' → {kind: 'hub', target: '_'}, 'workspace:finanze' → {kind: 'workspace', target: 'finanze'}
      if (scope === 'hub') return { kind: 'hub', target: '_' };
      if (scope && scope.startsWith('workspace:')) return { kind: 'workspace', target: scope.split(':', 2)[1] };
      return { kind: 'hub', target: '_' };
    },

    async loadGoals() {
      this.goalsState.loading = true;
      try {
        const params = new URLSearchParams();
        const sc = this._currentGoalsScope();
        if (sc) params.append('scope', sc);
        if (this.goalsState.filterStatus) params.append('status', this.goalsState.filterStatus);
        const data = await this.fetchJson(`/api/goals?${params}`);
        this.goalsState.list = data.goals || [];
      } catch (e) {
        console.error('loadGoals failed', e);
        this.goalsState.list = [];
      } finally {
        this.goalsState.loading = false;
        this.refreshIcons();
      }
    },

    // ===== Phase A — Diff preview helpers =====
    GOAL_WHITELIST_FIELDS: new Set([
      'title','deadline','status','priority','responsabile',
      'success_criteria','judge_cron','judge_model','judge_provider','judge_effort',
      'tags','linked_tasks','linked_routines',
      'assigned_agents','escalation_to','escalation_trigger','escalated',
      'responsabile_llm','escalation_llm','judge_agent',
      'anti_patterns','judge_rubric',
      'autonomy_level','pipeline_cron','execution_budget',
    ]),
    _isValidCron(expr) {
      if (typeof expr !== 'string') return false;
      const e = expr.trim();
      // Basic 5-field check + reject trailing garbage
      const parts = e.split(/\s+/);
      if (parts.length !== 5) return false;
      // Permissive regex (matcher: digit, *, /, -, ,)
      return parts.every(p => /^[\d\*\/\-,]+$/.test(p));
    },
    suggestionFieldOk(goalId, field, value) {
      if (!this.GOAL_WHITELIST_FIELDS.has(field)) {
        return { ok: false, reason: 'non-whitelisted field, will be dropped' };
      }
      if ((field === 'judge_cron' || field === 'pipeline_cron') && value) {
        if (!this._isValidCron(String(value))) {
          return { ok: false, reason: `invalid cron — expected "min hr dom mon dow"` };
        }
      }
      if (field === 'autonomy_level') {
        const v = parseInt(value, 10);
        if (![0,1,2,3].includes(v)) {
          return { ok: false, reason: 'autonomy_level out of range 0-3' };
        }
      }
      return { ok: true, reason: '' };
    },

    // ===== M2 — Office layout helpers =====
    lastVerdictOf(goalData) {
      const entries = (goalData?.journal_entries) || [];
      if (!entries.length) return '';
      return entries[entries.length - 1].verdict || '';
    },
    lastVerdictForAgent(goalData, agentName) {
      if (!agentName) return '';
      const entries = (goalData?.journal_entries) || [];
      for (let i = entries.length - 1; i >= 0; i--) {
        if ((entries[i].agent || '').toLowerCase() === agentName.toLowerCase()) {
          return entries[i].verdict + ' (' + entries[i].ts + ')';
        }
      }
      return '';
    },
    agentLlmLabel(llm) {
      if (!llm || (typeof llm !== 'object')) return '(default LLM)';
      const p = llm.provider || '?';
      const m = llm.model || '?';
      const e = llm.effort ? '/' + llm.effort : '';
      if (p === '?' && m === '?') return '(default LLM)';
      return p + '/' + m + e;
    },
    verdictBarStyle(verdict) {
      const map = {
        on_track: 'background: #22c55e;',
        achieved: 'background: #16a34a;',
        drift:    'background: #f59e0b;',
        blocked:  'background: #ef4444;',
        failed:   'background: #991b1b;',
      };
      return map[verdict] || 'background: #6b7280;';
    },
    // ===== D2 — Monitor scripts =====
    async loadScripts() {
      if (!this.goalsState.detail.open) return;
      try {
        const { kind, target } = this._splitScope(this.goalsState.detail.scope);
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(this.goalsState.detail.id)}/scripts`);
        if (!r.ok) return;
        const data = await r.json();
        this.goalsState.detail.scripts = data?.scripts || [];
        this.refreshIcons();
      } catch (e) { /* ignore */ }
    },
    async scriptStart(path) {
      try {
        const { kind, target } = this._splitScope(this.goalsState.detail.scope);
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(this.goalsState.detail.id)}/scripts/start`, {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ path })
        });
        const data = await r.json();
        if (data.ok) this.showToast('success', `Started pid=${data.pid}`);
        else this.showToast('error', 'Start failed', data.error);
        await this.loadScripts();
      } catch (e) { this.showToast('error', 'Error', e.message); }
    },
    async scriptStop(path) {
      try {
        const { kind, target } = this._splitScope(this.goalsState.detail.scope);
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(this.goalsState.detail.id)}/scripts/stop`, {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ path })
        });
        const data = await r.json();
        if (data.ok) this.showToast('success', 'Stopped');
        await this.loadScripts();
      } catch (e) { this.showToast('error', 'Error', e.message); }
    },
    async scriptLog(path) {
      try {
        const { kind, target } = this._splitScope(this.goalsState.detail.scope);
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(this.goalsState.detail.id)}/scripts/log?path=${encodeURIComponent(path)}&tail=200`);
        const data = await r.json();
        this.goalsState.detail.scriptLog = data?.log || '(empty log)';
        this.goalsState.detail.scriptLogFile = path.split('/').pop();
      } catch (e) { this.showToast('error', 'Error', e.message); }
    },

    // ===== Phase B — Pending actions =====
    async loadPendingActions() {
      if (!this.goalsState.detail.open) return;
      try {
        const { kind, target } = this._splitScope(this.goalsState.detail.scope);
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(this.goalsState.detail.id)}/pending-actions?status=pending`);
        if (!r.ok) return;
        const data = await r.json();
        this.goalsState.detail.pendingActions = data?.actions || [];
        this.refreshIcons();
      } catch (e) { /* ignore */ }
    },

    startPendingActionsPolling() {
      this.stopPendingActionsPolling();
      this.goalsState.detail.pendingPollTimer = setInterval(() => {
        if (this.goalsState.detail.open) this.loadPendingActions();
        else this.stopPendingActionsPolling();
      }, 5000);
    },

    stopPendingActionsPolling() {
      if (this.goalsState.detail.pendingPollTimer) {
        clearInterval(this.goalsState.detail.pendingPollTimer);
        this.goalsState.detail.pendingPollTimer = null;
      }
    },

    async resolveAction(actionId, verdict) {
      try {
        const { kind, target } = this._splitScope(this.goalsState.detail.scope);
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(this.goalsState.detail.id)}/pending-actions/${encodeURIComponent(actionId)}/resolve`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ verdict }),
        });
        if (!r.ok) throw new Error(await r.text());
        this.showToast('success', `Action ${verdict}`);
        await this.loadPendingActions();
      } catch (e) {
        this.showToast('error', 'Resolve failed', e.message);
      }
    },

    actionExpiresIn(action) {
      const expTs = action?.expires_at_ts;
      if (!expTs) return '?';
      const nowSec = Date.now() / 1000;
      const remain = expTs - nowSec;
      if (remain <= 0) return 'SCADUTA';
      const min = Math.floor(remain / 60);
      if (min >= 1) return `${min}m`;
      return `${Math.floor(remain)}s`;
    },

    autonomyLabel(level) {
      const labels = {
        0: 'L0 Observer — read-only, no side-effect',
        1: 'L1 Advisor — auto-kanban + suggestions (default)',
        2: 'L2 Gated — proposes actions → approval Telegram/UI',
        3: 'L3 Autonomous — runs on its own within budget',
      };
      return labels[level] ?? labels[1];
    },
    activityLineColor(level) {
      return ({
        'info':    '#c9d1d9',
        'success': '#3fb950',
        'warn':    '#d29922',
        'error':   '#f85149',
        'tool':    '#58a6ff',
      })[level] || '#c9d1d9';
    },
    clearActivityBoard() {
      this.activityLog = [];
    },

    _activityBoardScroll() {
      // Autoscroll al bottom dopo nuovo evento
      const el = this.$refs && this.$refs.activityLog;
      if (el) setTimeout(() => { el.scrollTop = el.scrollHeight; }, 0);
    },

    connectActivityWs(scope, goalId) {
      // M3 — WS stream live activity events
      this.disconnectActivityWs();
      const { kind, target } = this._splitScope(scope);
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${proto}//${location.host}/ws/goals/${kind}/${target}/${encodeURIComponent(goalId)}/activity`;
      try {
        const ws = new WebSocket(url);
        this.activityWs = ws;
        ws.onopen = () => {
          this.activityBoardConnected = true;
        };
        ws.onmessage = (ev) => {
          try {
            const data = JSON.parse(ev.data);
            if (data.type === 'snapshot') {
              this.activityLog = (data.events || []);
              this._activityBoardScroll();
            } else if (data.type === 'events') {
              this.activityLog = [...this.activityLog, ...(data.events || [])];
              // Cap log lunghezza a 1000 lines
              if (this.activityLog.length > 1000) {
                this.activityLog = this.activityLog.slice(-1000);
              }
              this._activityBoardScroll();
            }
          } catch (e) {
            console.error('[activity-ws] parse', e);
          }
        };
        ws.onerror = (e) => {
          console.warn('[activity-ws] error', e);
          this.activityBoardConnected = false;
        };
        ws.onclose = () => {
          this.activityBoardConnected = false;
        };
      } catch (e) {
        console.error('[activity-ws] connect failed', e);
      }
    },

    disconnectActivityWs() {
      if (this.activityWs) {
        try { this.activityWs.close(); } catch (e) {}
        this.activityWs = null;
      }
      this.activityBoardConnected = false;
    },
    async runGoalPipeline() {
      // F4 — Run office pipeline (analyst → risk-officer → executor)
      const meta = this.goalsState.detail.data?.meta;
      if (!meta) return;
      this.goalsState.pipelineRunning = true;
      try {
        const { kind, target } = this._splitScope(meta.scope || this.goalsState.detail.scope);
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(meta.id || this.goalsState.detail.id)}/pipeline`, {
          method: 'POST',
        });
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        this.showToast('success', `Pipeline → ${data.verdict || '?'}`, `Ruoli: ${(data.roles_invoked || []).join(' → ')}`);
        await this.openGoalDetail({ scope: meta.scope, id: meta.id });
        await this.loadGoals();
      } catch (e) {
        this.showToast('error', 'Pipeline failed', (e.message || '').substring(0, 200));
      } finally {
        this.goalsState.pipelineRunning = false;
      }
    },

    async runGoalJudgePerAgent(goalMeta, agentName) {
      // M4 — Endpoint per-agent: usa LLM dedicato dell'agent
      if (!agentName) return this.runGoalJudge(goalMeta);
      this.goalsState.judging = goalMeta.id;
      try {
        const { kind, target } = this._splitScope(goalMeta.scope);
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(goalMeta.id)}/judge/${encodeURIComponent(agentName)}`, {
          method: 'POST',
        });
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        this.showToast('success', `${agentName} → ${data.verdict || '?'}`, data.summary || '');
        // Reload detail
        await this.openGoalDetail({ scope: goalMeta.scope, id: goalMeta.id });
        await this.loadGoals();
      } catch (e) {
        this.showToast('error', `Judge ${agentName} failed`, e.message);
      } finally {
        this.goalsState.judging = '';
      }
    },

    async openGoalDetail(g) {
      this.goalsState.detail.open = true;
      this.goalsState.detail.loading = true;
      this.goalsState.detail.scope = g.scope;
      this.goalsState.detail.id = g.id;
      this.goalsState.detail.reflectText = '';
      // M3 — Reset + connect WS activity stream
      this.activityLog = [];
      this.connectActivityWs(g.scope, g.id);
      // F4 — Load specialist notes recenti
      this.goalsState.detail.notes = [];
      try {
        const { kind, target } = this._splitScope(g.scope);
        const ndata = await this.fetchJson(`/api/goals/${kind}/${target}/${encodeURIComponent(g.id)}/notes?limit=12`);
        this.goalsState.detail.notes = ndata?.notes || [];
      } catch (e) { /* ignore */ }
      // Phase B — Load + poll pending actions
      this.goalsState.detail.pendingActions = [];
      await this.loadPendingActions();
      this.startPendingActionsPolling();
      // D2 — Load monitor scripts
      this.goalsState.detail.scripts = [];
      this.goalsState.detail.scriptLog = '';
      await this.loadScripts();
      try {
        const { kind, target } = this._splitScope(g.scope);
        const data = await this.fetchJson(`/api/goals/${kind}/${target}/${encodeURIComponent(g.id)}`);
        this.goalsState.detail.data = data;
        // Fase 18.C.3 — fetch linked kanban tasks
        try {
          const lt = await this.fetchJson(`/api/goals/${kind}/${target}/${encodeURIComponent(g.id)}/linked-tasks`);
          this.goalsState.detail.linked_tasks = lt.tasks || [];
        } catch (e) {
          this.goalsState.detail.linked_tasks = [];
        }
      } catch (e) {
        console.error('openGoalDetail failed', e);
        this.goalsState.detail.data = null;
      } finally {
        this.goalsState.detail.loading = false;
        this.refreshIcons();
      }
    },

    async runGoalJudge(g) {
      this.goalsState.judging = g.id;
      try {
        const { kind, target } = this._splitScope(g.scope);
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(g.id)}/judge`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        if (!r.ok) {
          const t = await r.text();
          this.showToast('error', 'Judge failed', t.substring(0, 200));
          return;
        }
        const data = await r.json();
        this.showToast('success', `Judge: ${data.verdict}`, data.summary?.substring(0, 200) || '');
        // Refresh list + detail if open
        await this.loadGoals();
        if (this.goalsState.detail.open && this.goalsState.detail.id === g.id) {
          await this.openGoalDetail(g);
        }
      } catch (e) {
        this.showToast('error', 'Judge error', e.message);
      } finally {
        this.goalsState.judging = '';
      }
    },

    async addGoalReflection() {
      const txt = (this.goalsState.detail.reflectText || '').trim();
      if (!txt) return;
      const { kind, target } = this._splitScope(this.goalsState.detail.scope);
      try {
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(this.goalsState.detail.id)}/reflect`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: txt }),
        });
        if (!r.ok) throw new Error(await r.text());
        this.goalsState.detail.reflectText = '';
        this.showToast('success', 'Reflection saved');
      } catch (e) {
        this.showToast('error', 'Save failed', e.message);
      }
    },

    async archiveGoalDetail(outcome) {
      if (!confirm(`Confirm: mark this goal as '${outcome}'?`)) return;
      const { kind, target } = this._splitScope(this.goalsState.detail.scope);
      try {
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(this.goalsState.detail.id)}/archive`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ outcome }),
        });
        if (!r.ok) throw new Error(await r.text());
        this.showToast('success', `Goal ${outcome}`);
        this.goalsState.detail.open = false;
        await this.loadGoals();
      } catch (e) {
        this.showToast('error', 'Archive failed', e.message);
      }
    },

    async setGoalStatus(newStatus) {
      // F18-ux — pause/resume/reopen
      const { kind, target } = this._splitScope(this.goalsState.detail.scope);
      try {
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(this.goalsState.detail.id)}/update`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: newStatus }),
        });
        if (!r.ok) throw new Error(await r.text());
        this.showToast('success', `Goal → ${newStatus}`);
        // Reload detail
        await this.openGoalDetail({ scope: this.goalsState.detail.scope, id: this.goalsState.detail.id });
        await this.loadGoals();
      } catch (e) {
        this.showToast('error', 'Status change failed', e.message);
      }
    },

    async deleteGoalDetail() {
      if (!confirm('Delete this goal permanently? Will remove goal.md + journal + reflections. Irreversible operation.')) return;
      const { kind, target } = this._splitScope(this.goalsState.detail.scope);
      try {
        const r = await fetch(`/api/goals/${kind}/${target}/${encodeURIComponent(this.goalsState.detail.id)}`, {
          method: 'DELETE',
        });
        if (!r.ok) throw new Error(await r.text());
        this.showToast('success', '🗑 Goal deleted');
        this.goalsState.detail.open = false;
        await this.loadGoals();
      } catch (e) {
        this.showToast('error', 'Delete failed', e.message);
      }
    },

    async editGoalDetail() {
      // F18-ux — apri wizard pre-fill con goal corrente per editing
      const meta = this.goalsState.detail.data?.meta;
      if (!meta) return;
      const body = this.goalsState.detail.data?.body || '';
      // Cerca preset matching del cron
      const cron = meta.judge_cron || '';
      const matched = this.GOAL_CRON_PRESETS.find(p => p.expr === cron);
      const presetId = matched ? matched.id : (cron ? 'custom' : 'manual');
      await this.openGoalWizard();
      // openGoalWizard ha resettato il form — sovrascrivo con i valori del goal
      const ensureLlm = (l) => ({ provider: l?.provider || '', model: l?.model || '', effort: l?.effort || '' });
      this.goalsState.wizard.form = {
        title: meta.title || '',
        scope: meta.scope || this.goalsState.detail.scope,
        priority: meta.priority || 'medium',
        deadline: meta.deadline || '',
        success_criteria_text: (meta.success_criteria || []).join('\n'),
        anti_patterns_text: (meta.anti_patterns || []).join('\n'),
        judge_rubric: meta.judge_rubric || '',
        tags_text: (meta.tags || []).join('\n'),
        responsabile: meta.responsabile || '',
        responsabile_llm: ensureLlm(meta.responsabile_llm),
        judge_agent: meta.judge_agent || '',
        judge_provider: meta.judge_provider || '',
        judge_model: meta.judge_model || '',
        judge_effort: meta.judge_effort || '',
        judge_cron: cron,
        cron_preset: presetId,
        body_md: body,
        assigned_agents: (meta.assigned_agents || []).map(a => ({
          role: a.role || 'analyst',
          agent: a.agent || '',
          cadence: a.cadence || 'on_demand',
          llm: ensureLlm(a.llm),
        })),
        escalation_to: meta.escalation_to || '',
        escalation_llm: ensureLlm(meta.escalation_llm),
        // Phase A
        autonomy_level: typeof meta.autonomy_level === 'number' ? meta.autonomy_level : 1,
        pipeline_cron: meta.pipeline_cron || '',
        pipeline_cron_preset: meta.pipeline_cron ? 'custom' : '',
        execution_budget: meta.execution_budget || { max_trades_per_day: 5, max_position_size_pct: 2, max_daily_loss_usdt: 100 },
      };
      this.goalsState.wizard.editing = { kind: this._splitScope(this.goalsState.detail.scope).kind, target: this._splitScope(this.goalsState.detail.scope).target, id: this.goalsState.detail.id };
    },

    async openGoalWizard() {
      this.goalsState.wizard = {
        open: true, saving: false, error: '',
        editing: null,
        step: 0,
        form: {
          title: '', scope: this._currentGoalsScope() || 'hub',
          priority: 'medium', deadline: '',
          success_criteria_text: '', responsabile: '',
          responsabile_llm: { provider: '', model: '', effort: '' },
          judge_agent: '',
          judge_provider: '', judge_model: '', judge_effort: '',
          judge_cron: '0 18 * * 0',
          cron_preset: 'weekly_sun_18',
          anti_patterns_text: '',
          judge_rubric: '',
          tags_text: '',
          body_md: '',
          // Fase 18.B — Team
          assigned_agents: [],  // [{role, agent, cadence, llm:{provider,model,effort}}]
          escalation_to: '',
          escalation_llm: { provider: '', model: '', effort: '' },
          // Phase A — Autonomy + pipeline cron
          autonomy_level: 1,
          pipeline_cron: '',
          pipeline_cron_preset: '',
          execution_budget: { max_trades_per_day: 5, max_position_size_pct: 2, max_daily_loss_usdt: 100 },
        },
        agents: [],
      };
      // Load agents (hub + workspace) per i dropdown responsabile/team/escalation
      try {
        const proj = this._currentGoalsScope().startsWith('workspace:')
          ? this._currentGoalsScope().split(':', 2)[1]
          : '';
        const hubData = await this.fetchJson('/api/agents');
        let agents = (hubData?.agents || []).map(a => ({ name: a.name, scope: 'hub', label: a.name + ' (hub)' }));
        if (proj) {
          try {
            const projData = await this.fetchJson('/api/agents?project=' + encodeURIComponent(proj));
            const projAgents = (projData?.agents || []).map(a => ({ name: a.name, scope: 'project:' + proj, label: a.name + ' (project)' }));
            agents = [...projAgents, ...agents];
          } catch (e) { /* ignore */ }
        }
        this.goalsState.wizard.agents = agents;
      } catch (e) { console.error('[goal-wizard] agents load fail', e); }
      this.loadCloneableAgents();  // riusa stesso loader per il team specialists
    },

    onGoalCronPresetChange() {
      const preset = this.goalsState.wizard.form.cron_preset;
      if (preset === 'custom') return;  // keep current expression for manual edit
      const found = this.GOAL_CRON_PRESETS.find(p => p.id === preset);
      if (found) this.goalsState.wizard.form.judge_cron = found.expr;
    },

    onPipelineCronPresetChange() {
      // Phase A — pipeline_cron preset selector
      const preset = this.goalsState.wizard.form.pipeline_cron_preset;
      if (preset === 'custom') return;
      this.goalsState.wizard.form.pipeline_cron = preset;
    },

    addGoalAgent() {
      this.goalsState.wizard.form.assigned_agents.push({
        role: 'analyst', agent: '', cadence: 'on_demand',
        llm: { provider: '', model: '', effort: '' },
      });
    },

    removeGoalAgent(idx) {
      this.goalsState.wizard.form.assigned_agents.splice(idx, 1);
    },

    advanceGoalWizardStep() {
      // Validation step-by-step
      const f = this.goalsState.wizard.form;
      const s = this.goalsState.wizard.step;
      if (s === 0) {
        if (!f.title.trim()) { this.goalsState.wizard.error = 'Title required'; return; }
        if (!(f.success_criteria_text || '').trim()) { this.goalsState.wizard.error = 'At least one success criterion'; return; }
      }
      this.goalsState.wizard.error = '';
      this.goalsState.wizard.step = Math.min(this.goalWizardSteps.length - 1, s + 1);
      this.refreshIcons();
    },

    async submitGoalWizard() {
      const f = this.goalsState.wizard.form;
      if (!f.title.trim()) {
        this.goalsState.wizard.error = 'Title required';
        return;
      }
      this.goalsState.wizard.saving = true;
      this.goalsState.wizard.error = '';
      try {
        const sc = (f.success_criteria_text || '')
          .split('\n').map(s => s.trim()).filter(Boolean);
        const ap = (f.anti_patterns_text || '')
          .split('\n').map(s => s.trim()).filter(Boolean);
        const tags = (f.tags_text || '')
          .split('\n').map(s => s.trim()).filter(Boolean);
        // Pulisci LLM dict (rimuovi campi vuoti)
        const cleanLlm = (l) => {
          const o = {};
          if (l && l.provider) o.provider = l.provider;
          if (l && l.model) o.model = l.model;
          if (l && l.effort) o.effort = l.effort;
          return Object.keys(o).length ? o : null;
        };
        const validAgents = (f.assigned_agents || []).filter(a => a.agent && a.role)
          .map(a => ({ role: a.role, agent: a.agent, cadence: a.cadence || 'on_demand',
                       llm: cleanLlm(a.llm) || {} }));
        const editing = this.goalsState.wizard.editing || null;
        const commonPayload = {
          title: f.title.trim(),
          priority: f.priority,
          deadline: f.deadline || null,
          success_criteria: sc,
          anti_patterns: ap,
          judge_rubric: (f.judge_rubric || '').trim(),
          tags: tags,
          responsabile: f.responsabile || null,
          responsabile_llm: cleanLlm(f.responsabile_llm) || {},
          judge_agent: f.judge_agent || null,
          judge_provider: f.judge_provider || null,
          judge_model: f.judge_model || null,
          judge_effort: f.judge_effort || null,
          judge_cron: f.judge_cron.trim() || '',
          assigned_agents: validAgents,
          escalation_to: f.escalation_to || null,
          escalation_llm: cleanLlm(f.escalation_llm) || {},
          // Phase A
          autonomy_level: parseInt(f.autonomy_level, 10) || 1,
          pipeline_cron: (f.pipeline_cron || '').trim(),
          execution_budget: f.execution_budget || {},
        };
        let r;
        if (editing) {
          r = await fetch(`/api/goals/${editing.kind}/${editing.target}/${encodeURIComponent(editing.id)}/update`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(commonPayload),
          });
        } else {
          r = await fetch('/api/goals/create', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              ...commonPayload,
              scope: f.scope,
              body_md: f.body_md,
            }),
          });
        }
        if (!r.ok) {
          const t = await r.text();
          throw new Error(t.substring(0, 300));
        }
        this.goalsState.wizard.open = false;
        this.goalsState.wizard.editing = null;
        this.showToast('success', editing ? '✏️ Goal updated' : '🎯 Goal created');
        await this.loadGoals();
        // Se eravamo in editing, ricarica il detail
        if (editing && this.goalsState.detail.open) {
          await this.openGoalDetail({ scope: this.goalsState.detail.scope, id: editing.id });
        }
      } catch (e) {
        this.goalsState.wizard.error = e.message;
      } finally {
        this.goalsState.wizard.saving = false;
      }
    },

    // ===== Fase 7v.b — Anthropic Claude subscription (detection-only) =====
    // --- Claude subscription: sign-in from the UI (CLI login via host PTY) ---
    async startClaudeLogin() {
      this.claudeLogin.msg = ''; this.claudeLogin.busy = true;
      try {
        const res = await fetch('/api/claude-oauth/login/start', { method: 'POST' });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || 'could not start login');
        this.claudeLogin.authUrl = d.auth_url;
        this.claudeLogin.code = '';
        this.claudeLogin.pending = true;
        window.open(d.auth_url, '_blank');
      } catch (e) {
        this.claudeLogin.msg = 'Error: ' + (e.message || e);
      } finally {
        this.claudeLogin.busy = false;
        this.$nextTick(() => this.refreshIcons());
      }
    },

    async completeClaudeLogin() {
      this.claudeLogin.msg = ''; this.claudeLogin.busy = true;
      try {
        const res = await fetch('/api/claude-oauth/login/complete', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: this.claudeLogin.code }),
        });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || 'login failed');
        this.claudeLogin.pending = false;
        this.claudeLogin.code = '';
        this.showToast('success', 'Claude subscription connected', 'Chats and Telegram are back on the subscription.');
        await this.loadClaudeOauthStatus();
      } catch (e) {
        this.claudeLogin.msg = 'Error: ' + (e.message || e) + ' — check the code and try again.';
      } finally {
        this.claudeLogin.busy = false;
      }
    },

    async cancelClaudeLogin() {
      try { await fetch('/api/claude-oauth/login/cancel', { method: 'POST' }); } catch (e) { /* best-effort */ }
      this.claudeLogin.pending = false; this.claudeLogin.code = ''; this.claudeLogin.msg = '';
    },

    async loadClaudeOauthStatus() {
      try {
        const s = await this.fetchJson('/api/claude-oauth/status');
        this.claudeOauthState.subscription_active = !!s.subscription_active;
        this.claudeOauthState.cli_installed = s.cli_installed !== false;
        this.claudeOauthState.account = s.account || '';
        this.claudeOauthState.api_key_set = !!s.api_key_set;
        this.claudeOauthState.platform = s.platform || '';
        this.claudeOauthState.storage_hint = s.storage_hint || '';
        this.claudeOauthState.precedence = s.precedence || '';
      } catch (e) {
        console.error('loadClaudeOauthStatus failed', e);
      }
    },

    // ===== Fase 7v — OpenAI ChatGPT subscription =====
    async loadOpenaiOauthStatus() {
      try {
        const s = await this.fetchJson('/api/openai-oauth/status');
        this.openaiOauthState.configured = !!s.configured;
        this.openaiOauthState.account_id_short = s.account_id_short || '';
        this.openaiOauthState.last_refresh = s.last_refresh || '';
        this.openaiOauthState.expired = !!s.expired;
        this.openaiOauthState.supported_models = s.supported_models || [];
        this.openaiOauthState.anja_enabled = !!s.anja_enabled;
        this.openaiOauthState.use_codex_cli = s.use_codex_cli !== false;
        this.openaiOauthState.auth_path = s.auth_path || '';
      } catch (e) {
        console.error('loadOpenaiOauthStatus failed', e);
      }
    },

    async saveOpenaiOauthConfig() {
      this.openaiOauthState.saving = true;
      this.openaiOauthState.message = '';
      this.openaiOauthState.error = false;
      try {
        const r = await fetch('/api/openai-oauth/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: this.openaiOauthState.anja_enabled }),
        });
        if (!r.ok) {
          const txt = await r.text();
          throw new Error(txt || `HTTP ${r.status}`);
        }
        this.openaiOauthState.message = this.openaiOauthState.anja_enabled
          ? '✓ ChatGPT subscription enabled for anja'
          : '✓ ChatGPT subscription disabled';
      } catch (e) {
        this.openaiOauthState.error = true;
        this.openaiOauthState.message = `✗ ${e.message || 'save failed'}`;
        // Revert toggle on failure
        this.openaiOauthState.anja_enabled = !this.openaiOauthState.anja_enabled;
      } finally {
        this.openaiOauthState.saving = false;
      }
    },

    async refreshOpenaiToken() {
      this.openaiOauthState.refreshing = true;
      this.openaiOauthState.message = '';
      this.openaiOauthState.error = false;
      try {
        const r = await fetch('/api/openai-oauth/refresh', { method: 'POST' });
        if (!r.ok) {
          const txt = await r.text();
          throw new Error(txt || `HTTP ${r.status}`);
        }
        const data = await r.json();
        if (data.summary) {
          Object.assign(this.openaiOauthState, {
            last_refresh: data.summary.last_refresh || '',
            expired: !!data.summary.expired,
          });
        }
        this.openaiOauthState.message = '✓ Token refreshed';
      } catch (e) {
        this.openaiOauthState.error = true;
        this.openaiOauthState.message = `✗ ${e.message || 'refresh failed'}`;
      } finally {
        this.openaiOauthState.refreshing = false;
      }
    },

    // ===== Fase 7t — Ollama local models =====
    async loadOllamaConfig() {
      try {
        const cfg = await this.fetchJson('/api/ollama/config');
        this.ollamaState.enabled = !!cfg.enabled;
        this.ollamaState.base_url = cfg.base_url || 'http://localhost:11434';
        // Ping non-blocking
        await this.refreshOllamaStatus();
      } catch (e) {
        console.error('loadOllamaConfig failed', e);
      }
    },

    async refreshOllamaStatus() {
      try {
        const st = await this.fetchJson('/api/ollama/status');
        this.ollamaState.online = !!st.online;
        this.ollamaState.error = st.error || '';
        this.ollamaState.statusChecked = true;
        if (st.online) {
          await this.refreshOllamaModels(false);
        } else {
          this.ollamaState.models = [];
        }
      } catch (e) {
        this.ollamaState.online = false;
        this.ollamaState.error = e.message || 'status failed';
        this.ollamaState.statusChecked = true;
      }
    },

    async refreshOllamaModels(force) {
      this.ollamaState.refreshing = true;
      try {
        const url = `/api/ollama/models${force ? '?refresh=1' : ''}`;
        const data = await this.fetchJson(url);
        this.ollamaState.models = data.models || [];
        this.ollamaState.online = !!data.online || data.models?.length > 0;
        if (data.error) this.ollamaState.error = data.error;
      } catch (e) {
        this.ollamaState.error = e.message || 'fetch failed';
      } finally {
        this.ollamaState.refreshing = false;
      }
    },

    async testOllama() {
      this.ollamaState.testing = true;
      this.ollamaState.message = '';
      try {
        await this.refreshOllamaStatus();
        if (this.ollamaState.online) {
          this.ollamaState.message = `✓ Endpoint reachable, ${this.ollamaState.models.length} models`;
          this.ollamaState.error = '';
        } else {
          this.ollamaState.message = `✗ ${this.ollamaState.error || 'Endpoint not reachable'}`;
        }
      } finally {
        this.ollamaState.testing = false;
      }
    },

    async saveOllamaConfig() {
      this.ollamaState.saving = true;
      this.ollamaState.message = '';
      try {
        const r = await fetch('/api/ollama/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            enabled: this.ollamaState.enabled,
            base_url: this.ollamaState.base_url,
          }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        await r.json();
        this.ollamaState.message = '✓ Configuration saved';
        await this.refreshOllamaStatus();
        this.refreshIcons();
      } catch (e) {
        this.ollamaState.message = `✗ ${e.message || 'save failed'}`;
      } finally {
        this.ollamaState.saving = false;
      }
    },

    // ===== Fase 11 RT — Realtime voice call =====

    get rtTimerLabel() {
      if (!this.rtState.startedAt) return '00:00';
      const sec = Math.floor((this.rtState.now - this.rtState.startedAt) / 1000);
      const mm = String(Math.floor(sec / 60)).padStart(2, '0');
      const ss = String(sec % 60).padStart(2, '0');
      return `${mm}:${ss}`;
    },

    // Stima prezzo OpenAI Realtime: ~$0.06/min input + ~$0.24/min output
    // Assumiamo 50/50 (parli metà tempo, ascolti metà) → ~$0.15/min media
    get rtPriceLabel() {
      if (!this.rtState.startedAt) return '$0.00';
      const minutes = (this.rtState.now - this.rtState.startedAt) / 60000;
      const est = minutes * 0.15; // stima media
      return `~$${est.toFixed(2)}`;
    },
    get rtPriceColor() {
      if (!this.rtState.startedAt) return 'var(--text-3)';
      const minutes = (this.rtState.now - this.rtState.startedAt) / 60000;
      if (minutes < 2)  return 'var(--text-2)';
      if (minutes < 5)  return '#eab308';  // giallo
      return '#ef4444';                     // rosso oltre 5 min
    },

    async startVoiceCall() {
      if (this.rtState.active) return;
      this.rtState.active = true;
      this.rtState.connected = false;
      this.rtState.error = '';
      this.rtState.transcript = [];
      this.rtState.startedAt = null;
      this.rtState.now = Date.now();

      try {
        // 1) Ottieni ephemeral key dal nostro server
        const r = await fetch('/api/realtime/session', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            conversation_id: this.currentConvId,
          }),
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${r.status}`);
        }
        const sess = await r.json();
        this.rtState.voice = sess.voice;
        this.rtState.model = sess.model;

        // 2) Mic capture
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.rtState._stream = stream;

        // xAI Realtime usa WebSocket (no WebRTC) → path dedicato
        if ((sess.provider || 'openai') === 'xai') {
          this.rtState.provider = 'xai';
          await this._startVoiceCallXaiWs(sess, stream);
          return;
        }
        this.rtState.provider = 'openai';

        // 3) PeerConnection
        const pc = new RTCPeerConnection();
        this.rtState._pc = pc;

        // <audio> per remote audio
        const audioEl = document.createElement('audio');
        audioEl.autoplay = true;
        this.rtState._audioEl = audioEl;
        pc.ontrack = (e) => {
          audioEl.srcObject = e.streams[0];
        };

        // Aggiungi mic come outbound track
        stream.getTracks().forEach(t => pc.addTrack(t, stream));

        // Data channel per eventi JSON (transcript, tool calls, ecc.)
        const dc = pc.createDataChannel('oai-events');
        this.rtState._dc = dc;
        dc.addEventListener('open', () => {
          console.log('[rt] data channel open');
          this.rtState.connected = true;
          this.rtState.startedAt = Date.now();
          this.rtState.tickerInterval = setInterval(() => {
            this.rtState.now = Date.now();
          }, 500);
        });
        dc.addEventListener('message', (e) => this.handleRealtimeEvent(e.data));

        // 4) SDP offer/answer con OpenAI direct
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        const sdpResp = await fetch(
          `https://api.openai.com/v1/realtime?model=${encodeURIComponent(sess.model)}`,
          {
            method: 'POST',
            body: offer.sdp,
            headers: {
              'Authorization': `Bearer ${sess.client_secret}`,
              'Content-Type': 'application/sdp',
              'OpenAI-Beta': 'realtime=v1',
            },
          }
        );
        if (!sdpResp.ok) {
          const errTxt = await sdpResp.text();
          throw new Error(`OpenAI SDP exchange failed: ${sdpResp.status} ${errTxt.slice(0, 200)}`);
        }
        const answerSdp = await sdpResp.text();
        await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
        console.log('[rt] WebRTC connection established');
      } catch (e) {
        this.rtState.error = String(e.message || e);
        console.error('[rt] startVoiceCall error:', e);
        this.showToast('error', 'Call failed', this.rtState.error);
        await this.endVoiceCall(false);
      }
    },

    // ===== xAI Realtime (WebSocket + PCM16 audio manuale) =====
    async _startVoiceCallXaiWs(sess, stream) {
      const AC = window.AudioContext || window.webkitAudioContext;
      // Auth browser: il token va come subprotocol (i browser non possono settare header WS)
      const ws = new WebSocket(sess.ws_url, ['realtime', 'xai-client-secret.' + sess.client_secret]);
      this.rtState._ws = ws;

      // Playback context (24kHz) + scheduling head per evitare gap/overlap
      const playCtx = new AC({ sampleRate: 24000 });
      this.rtState._playCtx = playCtx;
      this.rtState._playHead = 0;

      // Capture context: mic → PCM16 24kHz → base64 → input_audio_buffer.append
      const capCtx = new AC({ sampleRate: 24000 });
      this.rtState._capCtx = capCtx;
      console.log('[rt-xai] AudioContext sampleRate → cap:', capCtx.sampleRate, '· play:', playCtx.sampleRate, '(atteso 24000)');
      const srcNode = capCtx.createMediaStreamSource(stream);
      const proc = capCtx.createScriptProcessor(4096, 1, 1);
      this.rtState._proc = proc;
      proc.onaudioprocess = (e) => {
        if (this.rtState.muted || ws.readyState !== WebSocket.OPEN) return;
        // Resample dal rate REALE del context (spesso 48kHz, il browser non onora 24000) a 24kHz
        const f32 = this._resampleTo24k(e.inputBuffer.getChannelData(0), capCtx.sampleRate);
        const pcm16 = this._float32ToPcm16(f32);
        ws.send(JSON.stringify({
          type: 'input_audio_buffer.append',
          audio: this._arrayBufferToBase64(pcm16.buffer),
        }));
      };
      srcNode.connect(proc);
      proc.connect(capCtx.destination);

      ws.addEventListener('open', () => {
        const tools = sess.tools || [];
        ws.send(JSON.stringify({
          type: 'session.update',
          session: {
            instructions: sess.instructions || '',
            voice: sess.voice || 'eve',
            modalities: ['audio', 'text'],
            input_audio_format: 'pcm16',
            output_audio_format: 'pcm16',
            input_audio_transcription: { model: 'whisper-1' },
            turn_detection: { type: 'server_vad', threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 500 },
            tools: tools,
            tool_choice: tools.length ? 'auto' : 'none',
          },
        }));
        this.rtState.connected = true;
        this.rtState.startedAt = Date.now();
        this.rtState.tickerInterval = setInterval(() => { this.rtState.now = Date.now(); }, 500);
        console.log('[rt-xai] websocket open + session.update sent');
      });

      ws.addEventListener('message', (e) => {
        if (typeof e.data !== 'string') return;
        let ev;
        try { ev = JSON.parse(e.data); } catch { return; }
        if (!ev || !ev.type) return;
        const isAudioDelta = (ev.type === 'response.audio.delta' || ev.type === 'response.output_audio.delta');
        if (!isAudioDelta) {
          console.log('[rt-xai] ◀ event:', ev.type,
            ev.type === 'error' ? JSON.stringify(ev.error || ev).slice(0, 400) : '');
        }
        if (isAudioDelta && ev.delta) {
          this._playPcm16Chunk(ev.delta);
          this.rtState.speaking = true;
        } else {
          this.handleRealtimeEvent(e.data);
        }
      });

      ws.addEventListener('error', (err) => {
        console.error('[rt-xai] ws error', err);
        this.rtState.error = 'xAI Realtime WebSocket error';
        this.showToast('error', 'Call failed', this.rtState.error);
      });
      ws.addEventListener('close', () => {
        if (this.rtState.active) this.endVoiceCall(true);
      });
    },

    _resampleTo24k(f32, srcRate) {
      if (!srcRate || srcRate === 24000) return f32;
      const ratio = 24000 / srcRate;
      const outLen = Math.max(1, Math.round(f32.length * ratio));
      const out = new Float32Array(outLen);
      for (let i = 0; i < outLen; i++) {
        const pos = i / ratio;
        const i0 = Math.floor(pos);
        const i1 = Math.min(i0 + 1, f32.length - 1);
        out[i] = f32[i0] + (f32[i1] - f32[i0]) * (pos - i0);
      }
      return out;
    },
    _float32ToPcm16(f32) {
      const out = new Int16Array(f32.length);
      for (let i = 0; i < f32.length; i++) {
        const s = Math.max(-1, Math.min(1, f32[i]));
        out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      return out;
    },
    _arrayBufferToBase64(buf) {
      const bytes = new Uint8Array(buf);
      let bin = '';
      const CHUNK = 0x8000;
      for (let i = 0; i < bytes.length; i += CHUNK) {
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
      }
      return btoa(bin);
    },
    _base64ToInt16(b64) {
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new Int16Array(bytes.buffer);
    },
    _playPcm16Chunk(b64) {
      const ctx = this.rtState._playCtx;
      if (!ctx) return;
      const pcm16 = this._base64ToInt16(b64);
      const f32 = new Float32Array(pcm16.length);
      for (let i = 0; i < pcm16.length; i++) f32[i] = pcm16[i] / 0x8000;
      const audioBuf = ctx.createBuffer(1, f32.length, 24000);
      audioBuf.copyToChannel(f32, 0);
      const node = ctx.createBufferSource();
      node.buffer = audioBuf;
      node.connect(ctx.destination);
      const startAt = Math.max(ctx.currentTime, this.rtState._playHead || 0);
      node.start(startAt);
      this.rtState._playHead = startAt + audioBuf.duration;
    },

    // Invia un evento realtime via data channel (OpenAI) o WebSocket (xAI)
    _rtSend(obj) {
      const dc = this.rtState._dc;
      const ws = this.rtState._ws;
      if (dc && dc.readyState === 'open') dc.send(JSON.stringify(obj));
      else if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
    },

    handleRealtimeEvent(raw) {
      let ev;
      try { ev = JSON.parse(raw); } catch { return; }
      const type = ev.type || '';

      // User audio trascritto
      if (type === 'conversation.item.input_audio_transcription.completed') {
        const txt = (ev.transcript || '').trim();
        if (txt) {
          this.rtState.transcript.push({ role: 'user', content: txt });
        }
      }
      // Anja text delta
      else if (type === 'response.audio_transcript.delta') {
        this.rtState._currentAssistantBuf += (ev.delta || '');
      }
      else if (type === 'response.audio_transcript.done') {
        const txt = (ev.transcript || this.rtState._currentAssistantBuf || '').trim();
        if (txt) {
          this.rtState.transcript.push({ role: 'assistant', content: txt });
        }
        this.rtState._currentAssistantBuf = '';
        this.rtState.speaking = false;
      }
      else if (type === 'response.audio.delta') {
        this.rtState.speaking = true;
      }
      else if (type === 'response.audio.done' || type === 'response.done') {
        this.rtState.speaking = false;
      }
      // ===== Function calling (Fase 11 RT-tools) =====
      else if (type === 'response.function_call_arguments.done') {
        // Anja ha completato la chiamata di un tool MCP → esegui via backend
        this.executeMcpToolCall(ev.name, ev.arguments, ev.call_id);
      }
      else if (type === 'error') {
        console.error('[rt] event error:', ev);
        this.showToast('error', 'Realtime error', ev.error?.message || JSON.stringify(ev));
      }
    },

    async executeMcpToolCall(name, argumentsJson, callId) {
      console.log('[rt] tool call:', name, argumentsJson);
      // Mostra nel transcript che Anja sta usando un tool (UX feedback)
      this.rtState.transcript.push({
        role: 'assistant',
        content: `⚙️ (uso tool: ${name})`,
      });
      let resultText = '';
      try {
        const r = await fetch('/api/realtime/tool-call', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, arguments: argumentsJson }),
        });
        const data = await r.json();
        if (data.error) {
          resultText = JSON.stringify({ error: data.error });
        } else {
          resultText = data.result || '';
        }
      } catch (e) {
        resultText = JSON.stringify({ error: String(e.message || e) });
      }

      // Invia result via data channel (OpenAI) o WebSocket (xAI) + trigger response.create
      try {
        this._rtSend({
          type: 'conversation.item.create',
          item: {
            type: 'function_call_output',
            call_id: callId,
            output: resultText.slice(0, 8000),  // safety cap
          },
        });
        this._rtSend({ type: 'response.create' });
      } catch (e) {
        console.error('[rt] tool result send error:', e);
      }
    },

    toggleMute() {
      const stream = this.rtState._stream;
      if (!stream) return;
      this.rtState.muted = !this.rtState.muted;
      stream.getAudioTracks().forEach(t => t.enabled = !this.rtState.muted);
    },

    async endVoiceCall(persist = true) {
      if (this.rtState.tickerInterval) {
        clearInterval(this.rtState.tickerInterval);
        this.rtState.tickerInterval = null;
      }
      try {
        if (this.rtState._dc) { try { this.rtState._dc.close(); } catch {} }
        if (this.rtState._pc) { try { this.rtState._pc.close(); } catch {} }
        if (this.rtState._ws) { try { this.rtState._ws.close(); } catch {} }
        if (this.rtState._proc) { try { this.rtState._proc.disconnect(); } catch {} }
        if (this.rtState._capCtx) { try { this.rtState._capCtx.close(); } catch {} }
        if (this.rtState._playCtx) { try { this.rtState._playCtx.close(); } catch {} }
        if (this.rtState._stream) {
          this.rtState._stream.getTracks().forEach(t => t.stop());
        }
        if (this.rtState._audioEl) {
          try { this.rtState._audioEl.srcObject = null; } catch {}
        }
      } catch (e) { console.warn('[rt] cleanup error', e); }

      // Persist transcript se ci sono items
      if (persist && this.rtState.transcript.length > 0) {
        try {
          const duration = this.rtState.startedAt
            ? Math.floor((Date.now() - this.rtState.startedAt) / 1000)
            : 0;
          await fetch('/api/realtime/transcript', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              conversation_id: this.currentConvId || `voice-${Date.now()}`,
              transcript: this.rtState.transcript,
              duration_sec: duration,
              voice: this.rtState.voice,
            }),
          });
          this.showToast('success', 'Call saved', `${this.rtState.transcript.length} messages · ${duration}s`);
          // Reload conversation list
          await this.loadConversations();
        } catch (e) {
          console.error('[rt] persist transcript error', e);
        }
      }

      // Reset state
      this.rtState.active = false;
      this.rtState.connected = false;
      this.rtState.muted = false;
      this.rtState.speaking = false;
      this.rtState._pc = null;
      this.rtState._stream = null;
      this.rtState._dc = null;
      this.rtState._ws = null;
      this.rtState._proc = null;
      this.rtState._capCtx = null;
      this.rtState._playCtx = null;
      this.rtState._playHead = 0;
      this.rtState._audioEl = null;
      this.rtState._currentUserBuf = '';
      this.rtState._currentAssistantBuf = '';
      this.rtState.startedAt = null;
    },

    // Fase 11 — Compact current conversation
    async compactCurrentConversation() {
      if (!this.currentConvId || this.compactRunning) return;
      if (!confirm('Compact the current conversation? Previous history will be replaced by a summary.')) return;
      this.compactRunning = true;
      try {
        const r = await fetch(`/api/conversations/${encodeURIComponent(this.currentConvId)}/compact`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ keep_last_n: 2 }),
        });
        const data = await r.json();
        if (r.ok) {
          this.showToast('success', 'Compact OK',
            `${data.messages_before} → ${data.messages_after} messages`);
          // Reload conversation
          if (this.sdkSessionByConv) delete this.sdkSessionByConv[this.currentConvId];
          await this.selectConversation(this.currentConvId);
        } else {
          this.showToast('error', 'Compact failed', data.detail || JSON.stringify(data));
        }
      } catch (e) {
        this.showToast('error', 'Compact error', e.message);
      } finally {
        this.compactRunning = false;
      }
    },

    // Fase 13+ — Suggested questions (project scope)
    async loadSuggestedQuestions(regenerate = false) {
      if (!this.isProjectScope) return;
      const name = this.currentProjectScopeName;
      if (this.suggestedQuestions.project === name && this.suggestedQuestions.questions.length > 0 && !regenerate) return;
      if (regenerate) this.suggestedQuestions.regenerating = true;
      else this.suggestedQuestions.loading = true;
      try {
        const params = new URLSearchParams({ project: name });
        if (regenerate) params.set('regenerate', 'true');
        const data = await this.fetchJson(`/api/project/suggested-questions?${params}`);
        if (data && data.questions) {
          this.suggestedQuestions.project = name;
          this.suggestedQuestions.questions = data.questions;
          this.suggestedQuestions.generatedAt = data.generated_at || Date.now() / 1000;
        }
      } catch (e) {
        console.error('[suggested_q] load error', e);
      } finally {
        this.suggestedQuestions.loading = false;
        this.suggestedQuestions.regenerating = false;
      }
    },

    useSuggestedQuestion(q) {
      this.inputText = q;
      // Auto-send dopo brief delay per UX
      this.$nextTick(() => {
        if (this.inputText.trim()) {
          this.sendMessage();
        }
      });
    },

    // Fase 13+ — Auto-ingest load/save/clear
    async loadAutoIngest() {
      if (!this.isProjectScope) return;
      const name = this.currentProjectScopeName;
      try {
        const data = await this.fetchJson(`/api/project/auto-ingest/status?project=${encodeURIComponent(name)}`);
        if (!data) return;
        this.autoIngest.project = name;
        this.autoIngest.projectRoot = data.project_root || '';
        this.autoIngest.config = data.config || this.autoIngest.config;
        this.autoIngest.pending = data.pending || { files: [] };
        this.autoIngest.daemon = data.daemon;
        this.autoIngest.whitelistText = (data.config.whitelist || []).join('\n');
      } catch (e) {
        console.error('[autoIngest] load error', e);
      }
    },

    async saveAutoIngestConfig() {
      this.autoIngest.saving = true;
      try {
        const whitelist = (this.autoIngest.whitelistText || '')
          .split('\n').map(s => s.trim()).filter(Boolean);
        const r = await fetch('/api/project/auto-ingest/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project: this.currentProjectScopeName,
            enabled: !!this.autoIngest.config.enabled,
            mode: this.autoIngest.config.mode,
            poll_interval_sec: parseInt(this.autoIngest.config.poll_interval_sec) || 30,
            notify_telegram: !!this.autoIngest.config.notify_telegram,
            whitelist: whitelist,
          }),
        });
        const data = await r.json();
        if (r.ok) {
          this.showToast('success', 'Auto-ingest config saved',
            `${this.currentProjectScopeName}: ${this.autoIngest.config.enabled ? 'active' : 'inactive'}`);
          await this.loadAutoIngest();
        } else {
          this.showToast('error', 'Save failed', data.detail || 'unknown');
        }
      } catch (e) {
        this.showToast('error', 'Save error', e.message);
      } finally {
        this.autoIngest.saving = false;
      }
    },

    async runAutoIngest() {
      if (this.autoIngest.running) return;
      const fileCount = (this.autoIngest.pending.files || []).length;
      if (!fileCount) return;
      if (!confirm(`Run /anja-ingest on ${fileCount} files? May take several minutes.`)) return;
      this.autoIngest.running = true;
      this.autoIngest.runStatus = `${fileCount} files…`;
      try {
        const r = await fetch('/api/project/auto-ingest/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project: this.currentProjectScopeName }),
        });
        const data = await r.json();
        if (r.ok && data.ok) {
          const ok = (data.files_ingested || []).length;
          const errs = (data.errors || []).length;
          this.showToast(errs > 0 ? 'info' : 'success',
            `Ingest completed`,
            `${ok} ingested, ${errs} errors`);
          await this.loadAutoIngest();
        } else {
          this.showToast('error', 'Ingest failed', data.error || data.detail || 'unknown');
        }
      } catch (e) {
        this.showToast('error', 'Ingest error', e.message);
      } finally {
        this.autoIngest.running = false;
        this.autoIngest.runStatus = '';
      }
    },

    async clearAutoIngestPending() {
      if (!confirm('Empty pending queue? (files will not be deleted)')) return;
      try {
        const r = await fetch('/api/project/auto-ingest/clear-pending', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project: this.currentProjectScopeName }),
        });
        if (r.ok) {
          this.showToast('success', 'Pending cleared', '');
          await this.loadAutoIngest();
        }
      } catch (e) {
        this.showToast('error', 'Clear error', e.message);
      }
    },

    // Fase 13 — Project preferences load/save
    async loadProjectPrefs() {
      if (!this.isProjectScope) return;
      const name = this.currentProjectScopeName;
      try {
        const data = await this.fetchJson(`/api/project/preferences?project=${encodeURIComponent(name)}`);
        if (!data) return;
        this.projectPrefs.project = name;
        this.projectPrefs.default_provider = (data.preferences && data.preferences.default_provider) || '';
        this.projectPrefs.default_model = (data.preferences && data.preferences.default_model) || '';
        this.projectPrefs.default_effort = (data.preferences && data.preferences.default_effort) || '';
        this.projectPrefs.effective = data.effective;
        // Risolvi project root via projects list
        const p = (this.projects || []).find(x => x.name === name);
        this.projectPrefs.projectRoot = (p && p.path) || '';
      } catch (e) {
        this.projectPrefs.message = `Error: ${e.message}`;
      }
    },

    async saveProjectPrefs() {
      if (!this.isProjectScope) return;
      this.projectPrefs.saving = true;
      this.projectPrefs.message = '';
      try {
        const r = await fetch('/api/project/preferences', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project: this.currentProjectScopeName,
            default_provider: this.projectPrefs.default_provider,
            default_model: this.projectPrefs.default_model,
            default_effort: this.projectPrefs.default_effort,
          }),
        });
        const data = await r.json();
        if (r.ok) {
          this.projectPrefs.message = 'Saved';
          await this.loadProjectPrefs();
          this.showToast('success', 'Project preferences saved',
            `${this.currentProjectScopeName}: ${this.projectPrefs.effective.provider}/${this.projectPrefs.effective.model}`);
        } else {
          this.projectPrefs.message = `Error: ${data.detail || 'unknown'}`;
        }
      } catch (e) {
        this.projectPrefs.message = `Error: ${e.message}`;
      } finally {
        this.projectPrefs.saving = false;
      }
    },

    // Fase 11 — Audio: presets di model/voice per provider
    audioPresetDefaults(kind) {
      const presets = {
        stt: {
          openai: { model: 'whisper-1' },
          groq:   { model: 'whisper-large-v3-turbo' },
          xai:    { model: 'grok-voice-fast-1.0' },
        },
        tts: {
          openai:     { model: 'tts-1', voice: 'nova' },
          xai:        { model: 'grok-voice-fast-1.0', voice: 'ara' },
          elevenlabs: { model: 'eleven_multilingual_v2', voice: 'Rachel' },
          groq:       { model: 'playai-tts', voice: 'Aaliyah-PlayAI' },
        },
        realtime: {
          openai: { model: 'gpt-4o-realtime-preview', voice: 'alloy' },
          xai:    { model: 'grok-voice-think-fast-1.0', voice: 'ara' },
        },
      };
      const provider = this.audioConfig[kind].provider;
      const preset = (presets[kind] || {})[provider];
      if (!preset) return;
      this.audioConfig[kind].model = preset.model;
      if (preset.voice && 'voice' in this.audioConfig[kind]) {
        this.audioConfig[kind].voice = preset.voice;
      }
    },

    // Fase 11 — Audio config (STT/TTS/Realtime)
    async loadAudioConfig() {
      try {
        const data = await this.fetchJson('/api/settings/audio');
        if (!data) return;
        this.audioConfig.stt = data.stt || this.audioConfig.stt;
        this.audioConfig.tts = data.tts || this.audioConfig.tts;
        this.audioConfig.realtime = data.realtime || this.audioConfig.realtime;
      } catch (e) {}
    },

    async saveAudioConfig() {
      this.audioConfig.saving = true;
      this.audioConfig.message = '';
      try {
        const r = await fetch('/api/settings/audio', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            stt: this.audioConfig.stt,
            tts: this.audioConfig.tts,
            realtime: this.audioConfig.realtime,
          }),
        });
        const data = await r.json();
        if (r.ok) {
          this.audioConfig.message = 'Saved';
          this.showToast('success', 'Audio config saved',
            `STT: ${data.stt.model} · TTS: ${data.tts.model} · Realtime: ${data.realtime.enabled ? 'on' : 'off'}`);
        } else {
          this.audioConfig.message = `Error: ${data.detail || 'unknown'}`;
        }
      } catch (e) {
        this.audioConfig.message = `Error: ${e.message}`;
      } finally {
        this.audioConfig.saving = false;
      }
    },

    // Fase 11 — Hub defaults
    async loadHubDefaults() {
      try {
        const data = await this.fetchJson('/api/settings/defaults');
        if (!data) return;
        this.hubDefaults.provider = data.default_provider || 'claude';
        this.hubDefaults.model = data.default_model || 'sonnet';
        this.hubDefaults.effort = data.default_effort || 'off';
        await this.onHubDefaultProviderChange();
      } catch (e) {}
    },

    async onHubDefaultProviderChange() {
      const p = this.hubDefaults.provider;
      if (this.hubDefaults.modelsFor[p]) return;
      try {
        const data = await this.fetchJson(`/api/providers/${encodeURIComponent(p)}/models`);
        if (data && data.models) {
          this.hubDefaults.modelsFor[p] = data.models;
        }
      } catch (e) {}
    },

    async saveHubDefaults() {
      this.hubDefaults.saving = true;
      this.hubDefaults.message = '';
      try {
        const r = await fetch('/api/settings/defaults', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            default_provider: this.hubDefaults.provider,
            default_model: this.hubDefaults.model,
            default_effort: this.hubDefaults.effort,
          }),
        });
        const data = await r.json();
        if (r.ok) {
          this.hubDefaults.message = `Saved: ${data.default_provider} / ${data.default_model} / ${data.default_effort}`;
          this.showToast('success', 'Hub defaults saved', `${data.default_provider}/${data.default_model}`);
        } else {
          this.hubDefaults.message = `Error: ${data.detail || 'unknown'}`;
        }
      } catch (e) {
        this.hubDefaults.message = `Error: ${e.message}`;
      } finally {
        this.hubDefaults.saving = false;
      }
    },

    // Fase 11 M-Tg — Telegram inbound controls
    async loadTelegramStatus() {
      try {
        const data = await this.fetchJson('/api/telegram/status');
        if (!data) return;
        this.telegramStatus = data;
        this.telegramConfig.enabled = !!data.enabled;
        this.telegramConfig.allowed_chat_ids_str = (data.allowed_chat_ids || []).join(', ');
      } catch (e) {
        this.telegramStatus = { running: false, enabled: false, has_token: false, _error: e.message };
      }
    },

    async telegramAction(action) {
      try {
        const r = await fetch(`/api/telegram/${action}`, { method: 'POST' });
        const data = await r.json();
        if (r.ok) {
          this.telegramStatus = data;
          this.showToast('success', `Telegram ${action}`, data.running ? 'running' : 'stopped');
        } else {
          this.showToast('error', `Telegram ${action} failed`, data.detail || JSON.stringify(data));
        }
      } catch (e) {
        this.showToast('error', `Telegram ${action} error`, e.message);
      }
    },

    async saveTelegramConfig() {
      // Patch hub config.json telegram block via /api/telegram/config (POST)
      const allowed = (this.telegramConfig.allowed_chat_ids_str || '')
        .split(',').map(s => s.trim()).filter(Boolean).map(Number).filter(n => !isNaN(n));
      try {
        const r = await fetch('/api/telegram/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            enabled: !!this.telegramConfig.enabled,
            allowed_chat_ids: allowed,
          }),
        });
        const data = await r.json();
        if (r.ok) {
          this.showToast('success', 'Telegram config saved', `allow-list: ${allowed.length} chat_id`);
          await this.loadTelegramStatus();
        } else {
          this.showToast('error', 'Save failed', data.detail || JSON.stringify(data));
        }
      } catch (e) {
        this.showToast('error', 'Save error', e.message);
      }
    },

    async telegramLinkCode() {
      // Codice monouso dal server: la chat che lo manda al bot finisce in allow-list da sola.
      this.telegramLink.loading = true;
      try {
        const r = await fetch('/api/telegram/link-code', { method: 'POST' });
        const data = await r.json();
        if (!r.ok) {
          this.showToast('error', 'Link code failed', data.detail || JSON.stringify(data));
          return;
        }
        const before = (this.telegramStatus?.allowed_chat_ids || []).length;
        Object.assign(this.telegramLink, {
          code: data.code, deep_link: data.deep_link || '', bot_username: data.bot_username || '',
          expires_at: data.expires_at * 1000, linked: false,
        });
        if (this.telegramLink._timer) clearInterval(this.telegramLink._timer);
        this.telegramLink._timer = setInterval(async () => {
          this.telegramLink._tick++;
          if (Date.now() > this.telegramLink.expires_at) {   // scaduto: chiudi il box
            clearInterval(this.telegramLink._timer); this.telegramLink._timer = null;
            this.telegramLink.code = '';
            return;
          }
          if (this.telegramLink._tick % 3 !== 0) return;      // status ogni 3s
          await this.loadTelegramStatus();
          if ((this.telegramStatus?.allowed_chat_ids || []).length > before) {
            this.telegramLink.linked = true;
            clearInterval(this.telegramLink._timer); this.telegramLink._timer = null;
            this.showToast('success', 'Telegram chat linked', 'added to the allow-list');
          }
        }, 1000);
        this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
      } catch (e) {
        this.showToast('error', 'Link code error', e.message);
      } finally {
        this.telegramLink.loading = false;
      }
    },

    telegramLinkCountdown() {
      void this.telegramLink._tick;   // dipendenza reattiva: si aggiorna col timer
      const s = Math.max(0, Math.round((this.telegramLink.expires_at - Date.now()) / 1000));
      return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    },

    addChatIdToAllowList(cid) {
      const cur = (this.telegramConfig.allowed_chat_ids_str || '').split(',').map(s => s.trim()).filter(Boolean);
      if (cur.includes(String(cid))) return;
      cur.push(String(cid));
      this.telegramConfig.allowed_chat_ids_str = cur.join(', ');
      this.telegramConfig.enabled = true;
      this.showToast('info', `chat_id ${cid} added`, 'Click Save + reload to apply');
    },

    async loadSettings() {
      const data = await this.fetchJson('/api/settings/providers');
      if (!data) return;
      this.settingsState.providers = data.providers || [];
      this.settingsState.secretsPath = data.secrets_path || '';
      // Reset inputs (l'utente li riempie solo se vuole sostituire)
      this.settingsState.inputs = {};
      for (const p of this.settingsState.providers) {
        this.settingsState.inputs[p.env] = '';
      }
      this.settingsState.message = '';
      this.settingsState.error = false;
    },

    async loadCustomSecrets() {
      const data = await this.fetchJson('/api/settings/secrets');
      if (!data) return;
      this.customSecrets.list = data.secrets || [];
      this.customSecrets.newKey = '';
      this.customSecrets.newValue = '';
      this.customSecrets.message = '';
      this.customSecrets.error = false;
    },

    async addCustomSecret() {
      const key = (this.customSecrets.newKey || '').trim();
      const value = (this.customSecrets.newValue || '').trim();
      if (!key || !value) return;
      if (!/^[A-Z][A-Z0-9_]*$/.test(key)) {
        this.customSecrets.message = 'Key must be UPPERCASE (e.g. MY_API_KEY).';
        this.customSecrets.error = true;
        return;
      }
      try {
        const r = await fetch('/api/settings/secrets', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key, value }),
        });
        const data = await r.json();
        if (!r.ok) {
          this.customSecrets.message = data.detail || `HTTP ${r.status}`;
          this.customSecrets.error = true;
          return;
        }
        this.customSecrets.message = `${key} saved.`;
        this.customSecrets.error = false;
        await this.loadCustomSecrets();
      } catch (e) {
        this.customSecrets.message = e.message;
        this.customSecrets.error = true;
      }
    },

    async deleteCustomSecret(key) {
      if (!confirm(`Remove ${key}?`)) return;
      try {
        const r = await fetch('/api/settings/secrets', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key, value: '' }),
        });
        if (r.ok) {
          this.customSecrets.message = `${key} removed.`;
          await this.loadCustomSecrets();
        }
      } catch (e) {
        this.customSecrets.message = e.message;
        this.customSecrets.error = true;
      }
    },

    async saveSettings() {
      this.settingsState.saving = true;
      this.settingsState.message = '';
      this.settingsState.error = false;
      // Solo le keys con un valore effettivamente inserito
      const body = {};
      for (const [k, v] of Object.entries(this.settingsState.inputs)) {
        if (v && v.trim()) body[k] = v.trim();
      }
      if (Object.keys(body).length === 0) {
        this.settingsState.saving = false;
        this.settingsState.message = 'No changes to save.';
        return;
      }
      try {
        const r = await fetch('/api/settings/providers', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await r.json();
        if (!r.ok) {
          this.settingsState.message = data.detail || `HTTP ${r.status}`;
          this.settingsState.error = true;
        } else {
          this.settingsState.message = `Saved. ${(data.changed || []).length} keys updated.`;
          await this.loadSettings();
        }
      } catch (e) {
        this.settingsState.message = e.message;
        this.settingsState.error = true;
      } finally {
        this.settingsState.saving = false;
      }
    },

    async clearProviderKey(envName) {
      if (!confirm(`Remove the key ${envName}?`)) return;
      try {
        const r = await fetch('/api/settings/providers', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ [envName]: '' }),
        });
        if (r.ok) {
          this.settingsState.message = `${envName} removed.`;
          await this.loadSettings();
        }
      } catch (e) {
        this.settingsState.message = e.message;
        this.settingsState.error = true;
      }
    },

    // ===== MEMORY INSPECTOR =====
    async openMemoryInspector() {
      this.view = 'memory';
      // Fase 13 Workspace: auto-set scope al workspace corrente
      if (this.isProjectScope) {
        this.memScope = 'project';
        this.memTarget = this.currentProjectScopeName;
      } else if (this.memScope === 'project') {
        // se torniamo in hub, ripristina scope hub
        this.memScope = 'hub';
        this.memTarget = '';
      }
      await this.loadMemInspect();
      this.refreshIcons();
    },

    async loadMemInspect() {
      const params = new URLSearchParams({ scope: this.memScope, target: this.memTarget });
      const data = await this.fetchJson(`/api/memory/inspect?${params}`);
      if (data) {
        this.memInspect = data;
        // se cambia scope/target, ricarica anche il file selezionato
        await this.loadMemFile();
        // Fase 12 — auto-load user HOT quando scope=hub e c'è default_user
        if (data.scope === 'hub' && data.default_user) {
          this.userFileSlug = '';
          await this.loadUserFile();
        }
        // Fase 14 — auto-load dialectic
        await this.loadDialectic();
      }
    },

    // ============================================================
    // Fase 14 — Dialectic memory
    // ============================================================

    _currentDialecticScope() {
      if (this.memScope === 'project' && this.memTarget) return `project:${this.memTarget}`;
      return 'hub';
    },

    async loadDialectic() {
      const scope = this._currentDialecticScope();
      this.dialectic.scope = scope;
      const params = new URLSearchParams({ scope });
      try {
        const data = await this.fetchJson(`/api/dialectic?${params}`);
        if (data) {
          this.dialectic.slug = data.slug || '';
          this.dialectic.active = data.active || [];
          this.dialectic.promoted = data.promoted || [];
          this.dialectic.decayed = data.decayed || [];
          this.dialectic.never_promote = data.never_promote || [];
          this.dialectic.file = data.file || '';
          this.dialectic.exists = !!data.exists;
        }
        this.refreshIcons();
      } catch (e) {
        console.error('[dialectic] load err', e);
      }
    },

    async _dialecticAction(endpoint, payload, label) {
      this.dialectic.busy = true;
      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope: this._currentDialecticScope(), ...payload }),
        });
        if (!res.ok) {
          const txt = await res.text();
          throw new Error(`${res.status}: ${txt}`);
        }
        const data = await res.json();
        this.showToast('success', label, data.text || data.ok || '');
        await this.loadDialectic();
      } catch (e) {
        this.showToast('error', `${label} failed`, e.message || String(e));
      } finally {
        this.dialectic.busy = false;
      }
    },

    promoteObs(text) {
      return this._dialecticAction('/api/dialectic/promote', { text }, '✅ Promoted');
    },
    revertObs(text) {
      if (!confirm(`Remove "${text}" from USER.md and mark as anti-pattern?`)) return;
      return this._dialecticAction('/api/dialectic/revert', { text }, '↶ Reverted');
    },
    neverPromoteObs(text) {
      if (!confirm(`Never promote "${text}"?`)) return;
      return this._dialecticAction('/api/dialectic/never-promote', { text }, '🚫 Never');
    },
    restoreObs(text) {
      return this._dialecticAction('/api/dialectic/restore', { text }, '♻️ Restored');
    },
    distillDialectic() {
      return this._dialecticAction('/api/dialectic/distill', {}, '🧪 Distilled');
    },

    async setMemScope(scope, target = '') {
      this.memScope = scope;
      this.memTarget = target;
      this.memFileEditMode = false;
      this.memPreview = null;
      await this.loadMemInspect();
    },

    async loadMemFile() {
      if (!this.memFileSelected) return;
      const params = new URLSearchParams({
        scope: this.memScope, target: this.memTarget, filename: this.memFileSelected,
      });
      const text = await this.fetchText(`/api/memory/file?${params}`);
      this.memFileContent = text || '';
      this.memFileEditMode = false;
    },

    // Fase 12 — User profile load/save (HOT or DETAIL)
    async loadUserFile() {
      const params = new URLSearchParams({ detail: this.userFileKind === 'detail' ? '1' : '' });
      if (this.userFileSlug) params.set('slug', this.userFileSlug);
      try {
        const text = await this.fetchText(`/api/memory/user?${params}`);
        this.userFileContent = text || '';
      } catch (e) {
        this.userFileContent = '';
      }
      this.userFileEditMode = false;
    },

    async setUserFileKind(kind) {
      this.userFileKind = kind;
      await this.loadUserFile();
    },

    async saveUserFile() {
      this.userFileSaving = true;
      try {
        const r = await fetch('/api/memory/user', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            slug: this.userFileSlug || undefined,
            detail: this.userFileKind === 'detail',
            content: this.userFileContent,
          }),
        });
        const data = await r.json();
        if (r.ok) {
          this.showToast('success', `User ${this.userFileKind} saved`, `${data.size} bytes`);
          this.userFileEditMode = false;
          await this.loadMemInspect();
        } else {
          this.showToast('error', 'Save failed', data.detail || JSON.stringify(data));
        }
      } catch (e) {
        this.showToast('error', 'Save error', e.message);
      } finally {
        this.userFileSaving = false;
      }
    },

    async saveMemFile() {
      this.memFileSaving = true;
      try {
        const r = await fetch('/api/memory/file', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            scope: this.memScope, target: this.memTarget,
            filename: this.memFileSelected, content: this.memFileContent,
          }),
        });
        const data = await r.json();
        if (r.ok) {
          this.showToast('success', `${this.memFileSelected} saved`, `${data.size} bytes`);
          this.memFileEditMode = false;
          await this.loadMemInspect();
        } else {
          this.showToast('error', 'Save failed', data.detail || `HTTP ${r.status}`);
        }
      } catch (e) {
        this.showToast('error', 'Save failed', e.message);
      } finally {
        this.memFileSaving = false;
      }
    },

    async regenerateToolsMd() {
      this.memRegenerating = true;
      try {
        const r = await fetch('/api/memory/regenerate-tools-md', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope: this.memScope, target: this.memTarget }),
        });
        const data = await r.json();
        if (data.status === 'regenerated') {
          this.showToast('success', 'TOOLS.md regenerated', data.stdout || '');
          if (this.memFileSelected === 'TOOLS.md') await this.loadMemFile();
          await this.loadMemInspect();
        } else {
          this.showToast('error', 'Regenerate failed', data.stderr || `exit ${data.exit}`);
        }
      } catch (e) {
        this.showToast('error', 'Regenerate failed', e.message);
      } finally {
        this.memRegenerating = false;
      }
    },

    async runPreviewInjection() {
      this.memPreviewLoading = true;
      this.memPreview = null;
      try {
        const r = await fetch('/api/memory/preview-injection', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            scope: this.memScope, target: this.memTarget,
            user_prompt: this.memPreviewPrompt || '',
          }),
        });
        const data = await r.json();
        if (r.ok) this.memPreview = data;
        else this.showToast('error', 'Preview failed', data.detail || `HTTP ${r.status}`);
      } catch (e) {
        this.showToast('error', 'Preview failed', e.message);
      } finally {
        this.memPreviewLoading = false;
      }
    },

    formatRelativeTime(iso) {
      if (!iso) return null;
      try {
        const d = new Date(iso);
        const now = new Date();
        const diff = (now - d) / 1000;
        if (diff < 60) return `${Math.floor(diff)}s ago`;
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        if (diff < 7 * 86400) return `${Math.floor(diff / 86400)}d ago`;
        return d.toLocaleDateString();
      } catch (e) {
        return iso;
      }
    },

    actionStub(name) {
      alert(`Action "${name}" — endpoint POST in M2.`);
    },

    // ===== CHAT WEBSOCKET =====
    connectWs() {
      if (this.ws && this.wsConnected) return;
      try {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${proto}//${window.location.host}/api/chat`);
        this.ws.onopen = () => {
          this.wsConnected = true;
          console.log('[anja] WebSocket connected');
        };
        this.ws.onclose = () => {
          this.wsConnected = false;
          console.log('[anja] WebSocket disconnected');
        };
        this.ws.onerror = (e) => {
          console.error('[anja] WebSocket error:', e);
          this.wsConnected = false;
        };
        this.ws.onmessage = (e) => this.onWsMessage(e);
      } catch (err) {
        console.error('connectWs:', err);
        this.showToast('error', 'WebSocket failed', String(err));
      }
    },

    onWsMessage(event) {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (e) {
        console.error('WS parse:', e);
        return;
      }

      // F-Notify-5: snapshot di stream attivi al connect
      if (data.type === 'active_streams_snapshot') {
        const s = new Set();
        for (const ss of (data.streams || [])) {
          if (ss.conv_id && !ss.completed) s.add(ss.conv_id);
        }
        this.chatActiveStreams = s;
        return;
      }
      // F-Notify-5: track stream lifecycle per chat-list-item live dot.
      // Eventi (text/tool_use/done) accompagnati da _conv_id (resume) o riferiti a currentConvId.
      const evConv = data._conv_id || this.currentConvId;
      if (evConv) {
        if (data.type === 'done' || data.type === 'error') {
          if (this.chatActiveStreams.has(evConv)) {
            const ns = new Set(this.chatActiveStreams);
            ns.delete(evConv);
            this.chatActiveStreams = ns;
          }
        } else if (data.type === 'text' || data.type === 'tool_use') {
          if (!this.chatActiveStreams.has(evConv)) {
            const ns = new Set(this.chatActiveStreams);
            ns.add(evConv);
            this.chatActiveStreams = ns;
          }
        }
      }

      if (data.type === 'session_id') {
        if (this.currentConvId && data.session_id) {
          this.sdkSessionByConv[this.currentConvId] = data.session_id;
          try { localStorage.setItem('anja.sdkSessions', JSON.stringify(this.sdkSessionByConv)); } catch (e) {}
        }
        return;
      } else if (data.type === 'usage') {
        // Fase 7t — context window meter. Gauge = PICCO contesto (input per-chiamata),
        // non la somma cumulativa dei round-tool (che gonfia falsamente oltre il 100%).
        this.chatUsage.tokens = data.context_input_tokens || data.total_tokens || 0;
        this.chatUsage.ctx = data.context_window || 0;
        this.chatUsage.lastIn = data.context_input_tokens || data.input_tokens || 0;
        this.chatUsage.lastOut = data.output_tokens || 0;
        this.chatUsage.cacheRead = data.cache_read_tokens || 0;
        return;
      } else if (data.type === 'auto_compact') {
        // Smart auto-compact triggered (Fase 11 fix 2026-05-11)
        this.showToast('info', `🗜 Auto-compact (${data.reason})`,
          `${data.messages_before} → ${data.messages_after} msg`);
        // Reload conversation per UI sync
        if (this.currentConvId && this.sdkSessionByConv) {
          delete this.sdkSessionByConv[this.currentConvId];
        }
        this.selectConversation(this.currentConvId).catch(() => {});
        return;
      }

      // F-MultiChatView: dirama gli eventi di contenuto al pane giusto (primary o secondo).
      const toSecond = this.splitView && data._conv_id && data._conv_id === this.secondConvId
                       && data._conv_id !== this.currentConvId;
      const msgs = toSecond ? this.secondMessages : this.messages;
      const last = msgs.length > 0 ? msgs[msgs.length - 1] : null;

      if (data.type === 'text') {
        this.thinkingActive = false;
        if (last && last.role === 'claude') {
          last.content += data.content;
        } else {
          msgs.push({ role: 'claude', content: data.content });
        }
      } else if (data.type === 'thinking') {
        this.thinkingActive = true;
      } else if (data.type === 'todo.updated') {
        if (!toSecond) this.turnTodos = data.todos || [];
      } else if (data.type === 'subagent.started') {
        const chip = `\n\n<span class="tool-chip">🤖 ${data.label || 'subagent'}</span>\n\n`;
        if (last && last.role === 'claude') last.content += chip;
        else msgs.push({ role: 'claude', content: chip });
      } else if (data.type === 'subagent.completed') {
        const chip = `\n\n<span class="tool-chip">${data.is_error ? '⚠' : '✓'} subagent</span>\n\n`;
        if (last && last.role === 'claude') last.content += chip;
      } else if (data.type === 'tool.result') {
        if (data.is_error && last && last.role === 'claude') {
          last.content += `\n\n<span class="tool-chip">⚠ tool error</span>\n\n`;
        }
      } else if (data.type === 'plan.proposed') {
        const idx = (last && last.role === 'claude' && !last.content && this.chatStreaming)
          ? msgs.length - 1 : msgs.length;
        msgs.splice(idx, 0, { role: 'claude', content: '', plan: {
          request_id: data.request_id, plan: data.plan || '', resolved: null,
        }});
        if (!toSecond) this.showToast('info', 'Plan proposed', 'approve or request a revision');
        setTimeout(() => this.scrollChatToBottom(true), 0);
      } else if (data.type === 'diff.ready') {
        const idx = (last && last.role === 'claude' && !last.content && this.chatStreaming)
          ? msgs.length - 1 : msgs.length;
        msgs.splice(idx, 0, { role: 'claude', content: '', gitdiff: {
          branch: data.branch, base_ref: data.base_ref, files: data.files || [],
          additions: data.additions || 0, deletions: data.deletions || 0,
          commits: data.commits || 0, patch: null, showPatch: false, resolved: null,
        }});
        if (!toSecond) this.showToast('info', 'Diff ready',
          `${(data.files || []).length} file (+${data.additions}/−${data.deletions})`);
        setTimeout(() => this.scrollChatToBottom(true), 0);
      } else if (data.type === 'merge.completed') {
        for (let k = msgs.length - 1; k >= 0; k--) {
          if (msgs[k].gitdiff && !msgs[k].gitdiff.resolved) {
            msgs[k].gitdiff.resolved = data.decision
              + (data.ok === false ? ' (failed)' : '');
            break;
          }
        }
      } else if (data.type === 'plan.resolved') {
        for (let k = msgs.length - 1; k >= 0; k--) {
          if (msgs[k].plan && msgs[k].plan.request_id === data.request_id) {
            msgs[k].plan.resolved = data.decision; break;
          }
        }
      } else if (data.type === 'tool_use') {
        this.thinkingActive = false;
        // visualizzo come chip inline
        const toolHtml = `\n\n<span class="tool-chip">🔧 ${data.name}</span>\n\n`;
        if (last && last.role === 'claude') {
          last.content += toolHtml;
        } else {
          msgs.push({ role: 'claude', content: toolHtml });
        }
        if (!toSecond) this.showToast('info', `Tool used: ${data.name}`, '');
      } else if (data.type === 'permission.requested') {
        // F-AgentSessions Fase 2 — bolla con bottoni allow/always/deny
        const idx = (last && last.role === 'claude' && !last.content && this.chatStreaming)
          ? msgs.length - 1 : msgs.length;
        msgs.splice(idx, 0, { role: 'claude', content: '', perm: {
          request_id: data.request_id, tool: data.tool,
          target: data.target || '', resolved: null,
        }});
        if (!toSecond) this.showToast('info', 'Permesso richiesto', `${data.tool}: ${(data.target || '').slice(0, 60)}`);
        setTimeout(() => this.scrollChatToBottom(true), 0);
      } else if (data.type === 'permission.resolved') {
        for (let k = msgs.length - 1; k >= 0; k--) {
          if (msgs[k].perm && (msgs[k].perm.request_id === data.request_id
              || (!data.request_id && !msgs[k].perm.resolved))) {
            msgs[k].perm.resolved = data.decision;
            msgs[k].perm.by = data.by || '';
            break;
          }
        }
      } else if (data.type === 'done') {
        if (toSecond) { this.secondStreaming = false; this.refreshIcons(); return; }
        this.chatStreaming = false;
        this.thinkingActive = false;
        this.turnTodos = [];
        this.stopElapsedTimer();
        this.refreshIcons();
        // refresh conversations list — sia hub (sidebar) che project (banner)
        this.loadConversations();
        if (this.currentProject) {
          this.projectConversationsProject = null;  // force reload
          this.loadProjectConversations(this.currentProject);
        }
      } else if (data.type === 'error') {
        if (toSecond) {
          this.secondStreaming = false;
          if (last && last.role === 'claude' && !last.content) msgs.pop();
          this.refreshIcons();
          return;
        }
        this.chatStreaming = false;
        this.stopElapsedTimer();
        this.showToast('error', 'Chat error', data.message);
        if (last && last.role === 'claude' && !last.content) {
          msgs.pop();
        }
        this.refreshIcons();
      }
      // la chat segue il fondo mentre streama (fix: prima non scendeva)
      setTimeout(() => this.scrollChatToBottom(), 0);
    },

    startElapsedTimer() {
      this._streamingStartTs = Date.now();
      this.elapsedSec = 0;
      this._elapsedTimer = setInterval(() => {
        this.elapsedSec = Math.floor((Date.now() - this._streamingStartTs) / 1000);
      }, 200);
    },

    stopElapsedTimer() {
      if (this._elapsedTimer) {
        clearInterval(this._elapsedTimer);
        this._elapsedTimer = null;
      }
    },

    // ===== Fase 24 — Chat attachments =====

    attachmentIcon(category) {
      const m = {
        image: '🖼',
        pdf: '📄',
        docx: '📝',
        xlsx: '📊',
        text: '📋',
        audio: '🎤',
        binary: '📎',
      };
      return m[category] || '📎';
    },

    async _currentConvId() {
      // Fase 24-fix: genera conv_id al primo upload se non esiste, evita mismatch
      // tra upload (saved in /uploads/<X>/) e send (read da /uploads/<Y>/).
      if (!this.currentConvId) {
        const scope = this.currentChatScope;
        const prefix = scope === 'hub' ? 'hub' : 'proj-' + (this.currentProject || 'unknown');
        this.currentConvId = `${prefix}-${Date.now()}`;
      }
      return this.currentConvId;
    },

    async uploadAttachment(file) {
      this.attachmentsUploading++;
      try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('conv_id', await this._currentConvId());
        const r = await fetch('/api/chat/upload', { method: 'POST', body: formData });
        if (!r.ok) {
          const txt = await r.text();
          this.showToast('error', 'Upload failed', txt.substring(0, 200));
          return;
        }
        const desc = await r.json();
        this.attachments.push(desc);
      } catch (e) {
        this.showToast('error', 'Upload error', e.message);
      } finally {
        this.attachmentsUploading--;
      }
    },

    handleFileSelect(ev) {
      const files = Array.from(ev.target.files || []);
      for (const f of files) this.uploadAttachment(f);
      ev.target.value = '';
    },

    handleFileDrop(ev) {
      const files = Array.from(ev.dataTransfer.files || []);
      for (const f of files) this.uploadAttachment(f);
    },

    handlePaste(ev) {
      const items = ev.clipboardData?.items || [];
      for (const item of items) {
        if (item.kind === 'file') {
          const file = item.getAsFile();
          if (file) this.uploadAttachment(file);
        }
      }
    },

    toggleStickyAttachment(idx) {
      // F24.b — pin attachment per i turn successivi (non resetta a fine send)
      const a = this.attachments[idx];
      if (!a) return;
      a.sticky = !a.sticky;
    },

    async removeAttachment(idx) {
      const a = this.attachments[idx];
      if (!a) return;
      // Best-effort server cleanup
      try {
        await fetch('/api/chat/upload/delete', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            conv_id: await this._currentConvId(),
            saved_filename: a.saved_filename,
          }),
        });
      } catch (e) { /* ignore */ }
      this.attachments.splice(idx, 1);
    },

    // B3 — autocomplete for /skill and /bundle
    async _slashFetchSkills() {
      const sa = this.slashAutocomplete;
      if (sa.cachedSkills) return sa.cachedSkills;
      try {
        const r = await fetch('/api/skills');
        const j = await r.json();
        sa.cachedSkills = (j.skills || []).map(s => ({
          name: s.name, description: s.description || '',
          category: s.category || '', scope: s.scope || '',
        }));
      } catch (e) { sa.cachedSkills = []; }
      return sa.cachedSkills;
    },
    async _slashFetchBundles() {
      const sa = this.slashAutocomplete;
      if (sa.cachedBundles) return sa.cachedBundles;
      try {
        const r = await fetch('/api/bundles');
        const j = await r.json();
        sa.cachedBundles = (j.bundles || []).map(b => ({
          name: b.name, description: b.description || '',
          category: 'bundle', scope: 'hub',
        }));
      } catch (e) { sa.cachedBundles = []; }
      return sa.cachedBundles;
    },
    async onInputTextChanged() {
      const sa = this.slashAutocomplete;
      const txt = this.inputText || '';
      const m = txt.match(/^\/(skill|bundle)\s*(\S*)$/);
      if (!m) {
        sa.open = false;
        return;
      }
      const kind = m[1];
      const query = (m[2] || '').toLowerCase();
      if (sa.kind !== kind) {
        sa.kind = kind;
        sa.items = kind === 'skill' ? await this._slashFetchSkills() : await this._slashFetchBundles();
      }
      sa.filtered = sa.items
        .filter(it => !query || it.name.toLowerCase().includes(query))
        .slice(0, 10);
      sa.index = 0;
      sa.open = sa.filtered.length > 0;
    },
    slashAcceptCurrent() {
      const sa = this.slashAutocomplete;
      if (!sa.open || !sa.filtered.length) return false;
      const sel = sa.filtered[sa.index] || sa.filtered[0];
      this.inputText = `/${sa.kind} ${sel.name} `;
      sa.open = false;
      this.$nextTick(() => {
        const ta = document.querySelector('textarea[x-model="inputText"]');
        if (ta) { ta.focus(); ta.setSelectionRange(this.inputText.length, this.inputText.length); }
      });
      return true;
    },
    slashKeydown(ev) {
      const sa = this.slashAutocomplete;
      if (!sa.open) return false;
      if (ev.key === 'ArrowDown') {
        ev.preventDefault();
        sa.index = (sa.index + 1) % sa.filtered.length;
        return true;
      }
      if (ev.key === 'ArrowUp') {
        ev.preventDefault();
        sa.index = (sa.index - 1 + sa.filtered.length) % sa.filtered.length;
        return true;
      }
      if (ev.key === 'Tab' || (ev.key === 'Enter' && !ev.shiftKey)) {
        ev.preventDefault();
        return this.slashAcceptCurrent();
      }
      if (ev.key === 'Escape') {
        ev.preventDefault();
        sa.open = false;
        return true;
      }
      return false;
    },

    // Pod marketing: /pod <brief> → fan-out isolato multi-provider via /api/pod/run.
    // Ogni specialista gira in sessione separata (contesto isolato), il lead sintetizza.
    async runPod(brief) {
      if (this.currentChatScope === 'hub' || !this.currentProject) {
        this.showToast('error', 'Pod', 'Open a brand workspace (project scope) to use /pod');
        return;
      }
      if (!brief) { this.showToast('info', 'Pod', 'Usage: /pod <brief>'); return; }
      const ws = this.currentProject;
      if (!this.currentConvId) this.currentConvId = `proj-${ws}-${Date.now()}`;
      this.messages.push({ role: 'user', content: '/pod ' + brief });
      const bubble = { role: 'claude', content: '🔀 **Pod** di `' + ws + '`: pianifico e delego agli specialisti…' };
      this.messages.push(bubble);
      this.inputText = ''; this._resetChatInputHeight();
      this.chatStreaming = true;
      this.startElapsedTimer();
      try {
        const res = await fetch('/api/pod/run', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ workspace: ws, brief }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          bubble.content = '⚠️ Pod: ' + (data.error || data.detail || ('HTTP ' + res.status));
        } else {
          let md = '### 🔀 Pod — delegations\n';
          (data.assignments || []).forEach(a => { md += `- **${a.role}** — ${a.task}\n`; });
          md += '\n';
          (data.results || []).forEach(r => {
            const u = r.usage || {};
            md += `- ${r.ok ? '✓' : '✗'} **${r.role}** (${r.provider}/${r.model}, peak ${u.context_input_tokens || '?'} tok)\n`;
          });
          md += '\n---\n\n' + (data.synthesis || '_(no synthesis)_');
          bubble.content = md;
        }
      } catch (e) {
        bubble.content = '⚠️ Pod error: ' + e;
      } finally {
        this.chatStreaming = false;
        this.stopElapsedTimer();
      }
    },

    sendMessage() {
      const txt = this.inputText.trim();
      if (!txt) return;
      if (this.chatStreaming) {
        // F-AgentSessions Fase 1: messaggio durante il turno = steering (ASP)
        this.steerTurn(txt);
        return;
      }
      // Pod marketing: /pod <brief> → orchestrazione isolata (no websocket)
      if (txt.startsWith('/pod ')) { this.runPod(txt.slice(5).trim()); return; }
      if (!this.ws || !this.wsConnected) {
        this.connectWs();
        // Wait briefly for connection
        setTimeout(() => this.sendMessage(), 500);
        this.inputText = txt;
        return;
      }

      // Generate conv id if first message (with scope prefix)
      if (!this.currentConvId) {
        const scope = this.currentChatScope;
        const prefix = scope === 'hub' ? 'hub' : 'proj-' + this.currentProject;
        this.currentConvId = `${prefix}-${Date.now()}`;
      }

      // Fase 24 — Snapshot attachments per visual nel user message
      const attachmentsSnapshot = this.attachments.length > 0
        ? this.attachments.map(a => ({
            filename: a.filename, category: a.category,
            size_bytes: a.size_bytes, extracted_chars: a.extracted_chars,
          }))
        : null;
      this.messages.push({
        role: 'user',
        content: txt,
        ...(attachmentsSnapshot ? { attachments: attachmentsSnapshot } : {}),
      });
      this.messages.push({ role: 'claude', content: '' });  // empty bubble for streaming
      this.inputText = ''; this._resetChatInputHeight();
      this.chatStreaming = true;
      this.startElapsedTimer();
      this.showToast('info', `Calling ${this.selectedProvider}/${this.selectedModel}...`, 'starting, waiting for first token');

      const payload = {
        message: txt,
        conversation_id: this.currentConvId,
        model: this.selectedModel,
        provider: this.selectedProvider,
        scope: this.currentChatScope,
        enable_image_gen: !!this.enableImageGen,  // Fase 7s
        media_model: this.enableImageGen ? (this.mediaModel || '') : '',  // Fase 23.b
      };
      // Fase 24 — attach descriptors
      if (this.attachments.length > 0) {
        payload.attachments = this.attachments.map(a => ({
          file_id: a.file_id,
          saved_filename: a.saved_filename,
          filename: a.filename,
          category: a.category,
          mime: a.mime,
          size_bytes: a.size_bytes,
        }));
      }
      if (this.selectedEffort) payload.effort = this.selectedEffort;
      if (this.aspMode) payload.asp_mode = this.aspMode;   // F-ASP: mode sticky
      if (this.sdkSessionByConv[this.currentConvId]) {
        payload.sdk_session_id = this.sdkSessionByConv[this.currentConvId];
      }
      this.ws.send(JSON.stringify(payload));
      setTimeout(() => this.scrollChatToBottom(true), 0);
      // F24.b — Reset attachments dopo send, ma preserva i sticky per i turn successivi
      this.attachments = this.attachments.filter(a => a.sticky);
      this.refreshIcons();
    },

    // F-AgentSessions Fase 2 — risposta a una richiesta di permesso
    async respondPermission(msg, decision) {
      if (!msg.perm || msg.perm.resolved) return;
      try {
        const r = await fetch('/api/session/permission', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ request_id: msg.perm.request_id, decision }),
        });
        if (r.ok) {
          msg.perm.resolved = decision;   // l'evento WS conferma comunque
          return;
        }
        const d = await r.json().catch(() => ({}));
        this.showToast('error', 'Permesso', d.detail || `HTTP ${r.status}`);
      } catch (e) {
        this.showToast('error', 'Permesso', String(e));
      }
    },

    // Auto-scroll della chat durante lo streaming, semantica "follow":
    // segue il fondo finché l'utente non scrolla via (chatFollow, listener in
    // init). Niente soglia sul gap: gli eventi SDK arrivano a blocchi grossi
    // (un messaggio intero), la soglia falliva al primo chunk. Il contenitore
    // si trova risalendo dall'inner al primo antenato scrollabile (in alcuni
    // layout la catena flex non vincola .chat__messages → scrolla un antenato
    // o la pagina).
    scrollChatToBottom(force = false) {
      if (!force && !this.chatFollow) return;
      const inner = [...document.querySelectorAll('.chat__messages-inner')]
        .find(e => e.offsetParent);
      if (!inner) return;
      let el = inner.parentElement;
      while (el && el !== document.body
             && el.scrollHeight <= el.clientHeight + 4) {
        el = el.parentElement;
      }
      const target = (el && el !== document.body)
        ? el : (document.scrollingElement || document.documentElement);
      target.scrollTop = target.scrollHeight;
      if (force) this.chatFollow = true;
    },

    // F-ASP — permission_mode: preferenza sticky (localStorage) che viaggia
    // col payload di ogni turno e vale dalla CREAZIONE della sessione; se la
    // sessione è già viva si applica anche subito via session.set.
    async setAspMode() {
      try { localStorage.setItem('anja_asp_mode', this.aspMode || ''); } catch (e) {}
      const mode = this.aspMode || 'default';
      if (!this.currentConvId) {
        this.showToast('info', 'Permissions', `mode → ${mode} (from the next chat)`);
        return;
      }
      try {
        const r = await fetch('/api/session/set', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ conv_id: this.currentConvId, permission_mode: mode }),
        });
        const d = await r.json().catch(() => ({}));
        if (r.ok && d.ok) {
          this.showToast('info', 'Permissions', `mode → ${mode}`);
        } else {
          this.showToast('info', 'Permissions', `mode → ${mode} (applies from the next turn)`);
        }
      } catch (e) {
        this.showToast('error', 'Permissions', String(e));
      }
    },

    // F-AgentSessions Fase 4 — review del diff di sessione
    async toggleDiffPatch(msg) {
      const g = msg.gitdiff;
      if (!g) return;
      if (g.showPatch) { g.showPatch = false; return; }
      if (g.patch === null) {
        try {
          const r = await fetch(`/api/session/diff?conv_id=${encodeURIComponent(this.currentConvId)}`);
          const d = await r.json();
          g.patch = r.ok ? (d.patch || '(empty diff)') : (d.detail || `HTTP ${r.status}`);
        } catch (e) { g.patch = String(e); }
      }
      g.showPatch = true;
    },

    async respondMerge(msg, decision) {
      const g = msg.gitdiff;
      if (!g || g.resolved) return;
      if (decision === 'discard'
          && !confirm('Discard the session branch and all its changes?')) return;
      try {
        const r = await fetch('/api/session/merge', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ conv_id: this.currentConvId, decision }),
        });
        const d = await r.json().catch(() => ({}));
        if (r.ok && d.ok) {
          g.resolved = decision;   // l'evento WS conferma comunque
          this.showToast('info', 'Git session',
            decision === 'merge' ? `merged into ${d.into} (${d.merged_commit})` : 'branch discarded');
        } else {
          this.showToast('error', 'Git session', d.error || d.detail || `HTTP ${r.status}`);
        }
      } catch (e) {
        this.showToast('error', 'Git session', String(e));
      }
    },

    // F-AgentSessions Fase 3 — risposta a un piano proposto (plan mode)
    async respondPlan(msg, decision) {
      if (!msg.plan || msg.plan.resolved) return;
      const feedback = decision === 'replan'
        ? (prompt('Feedback for the plan revision (optional):') || '') : '';
      try {
        const r = await fetch('/api/session/plan', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ request_id: msg.plan.request_id, decision, feedback }),
        });
        if (r.ok) { msg.plan.resolved = decision; return; }
        const d = await r.json().catch(() => ({}));
        this.showToast('error', 'Plan', d.detail || `HTTP ${r.status}`);
      } catch (e) {
        this.showToast('error', 'Plan', String(e));
      }
    },

    // F-AgentSessions Fase 1 — steering: inietta il messaggio nel turno in corso.
    // Se ASP non è attivo (flag off / turno non-claude) degrada al toast di attesa.
    async steerTurn(txt) {
      try {
        const r = await fetch('/api/session/steer', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ conv_id: this.currentConvId, message: txt }),
        });
        if (r.ok) {
          const d = await r.json();
          if (d.ok) {
            // il messaggio utente entra PRIMA della bolla claude in streaming
            this.messages.splice(this.messages.length - 1, 0,
                                 { role: 'user', content: txt, steered: true });
            this.inputText = ''; this._resetChatInputHeight();
            this.showToast('info', 'Steering', 'injected into the current turn');
            return;
          }
        }
      } catch (e) { /* fallthrough */ }
      this.showToast('info', 'In progress', 'Wait for the response to finish');
    },

    // F-AgentSessions Fase 1 — stop: interrupt della sessione ASP,
    // fallback al cancel duro del task per il path non-ASP.
    async stopTurn() {
      const conv = this.currentConvId;
      if (!conv) return;
      try {
        const r = await fetch('/api/session/interrupt', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ conv_id: conv }),
        });
        if (r.ok) {
          const d = await r.json();
          if (d.ok) { this.showToast('info', 'Stop', 'turn interrupted'); return; }
        }
      } catch (e) { /* fallthrough */ }
      try {
        await fetch('/api/chat/cancel', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ conv_id: conv }),
        });
        this.showToast('info', 'Stop', 'stream canceled');
      } catch (e) { /* già fermo o server irraggiungibile */ }
    },

    navigateLink(link) {
      if (link.includes('/wiki/')) {
        const parts = link.split('/');
        if (parts.length >= 3) {
          const proj = parts[0];
          const page = parts[2];
          this.selectWorkspace(proj);
          this.currentPage = page;
          this.currentTab = this.guessTabForPage(page);
          this.loadCurrentPage();
        }
      } else if (this.currentProject) {
        this.currentPage = link;
        this.currentTab = this.guessTabForPage(link);
        this.loadCurrentPage();
      }
      this.refreshIcons();
    },

    guessTabForPage(page) {
      if (['index', 'overview', 'log'].includes(page)) return page.charAt(0).toUpperCase() + page.slice(1);
      // best-effort heuristic, the backend serve search through subdirs
      return 'Index';
    },

    getProject(name) {
      return this.projects.find(p => p.name === name);
    },

    renderMarkdown(content) {
      if (!content) return '';
      const escText = (s) => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
      let safe = content.replace(/<span class="tool-chip">([^<]+)<\/span>/g, '⟦TOOLCHIP:$1⟧');
      // Anti-XSS: il markdown può contenere HTML grezzo controllato dall'attaccante
      // (output LLM, wiki/kanban multi-utente, messaggi Telegram). Sanitizza SUBITO
      // l'output di marked; le trasformazioni fidate sotto (media/wikilink) aggiungono
      // markup controllato DOPO e non vengono ri-sanitizzate. Senza DOMPurify → solo testo.
      if (!window.DOMPurify) return escText(content).replace(/\n/g, '<br>');
      let html = window.DOMPurify.sanitize(marked.parse(safe));
      html = html.replace(/⟦TOOLCHIP:([^⟧]+)⟧/g, (m, name) => `<span class="tool-chip">${escText(name)}</span>`);
      // ls interpolato in onclick="...'${ls}'..." → neutralizza breakout di stringa JS/attributo
      const jsAttr = (s) => String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;').replace(/</g, '&lt;');
      html = html.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (m, link, alias) => {
        const display = escText(alias || link);
        const ls = jsAttr(link);
        return `<a class="wikilink" onclick="event.preventDefault(); window.app.navigateLink('${ls}')">${display}</a>`;
      });
      // Fase 23.c — Auto-render generated media inline (image + video)
      // 1) Direct web_url emesso da MCP: /api/media/{images,videos}/<date>/<file>
      html = html.replace(
        /<code>(\/api\/media\/(images|videos)\/(\d{4}-\d{2}-\d{2})\/([\w.\-]+\.(?:png|jpg|jpeg|webp|gif|mp4|webm|mov)))<\/code>/gi,
        (m, url, kind, date, file) => {
          if (kind === 'videos') {
            return `<video controls preload="metadata" style="max-width: 100%; max-height: 480px; border-radius: 8px; display: block; margin: 8px 0;"><source src="${url}" type="video/mp4">your browser does not support video</video>`;
          }
          return `<a href="${url}" target="_blank"><img src="${url}" alt="${file}" style="max-width: 480px; max-height: 480px; border-radius: 6px; display: block; margin: 8px 0;"></a>`;
        }
      );
      // 2) Bare URL (es. quando Anja scrive senza markdown link)
      html = html.replace(
        /(?<![">])(\/api\/media\/(images|videos)\/(\d{4}-\d{2}-\d{2})\/([\w.\-]+\.(?:png|jpg|jpeg|webp|gif|mp4|webm|mov)))(?![<"])/gi,
        (m, url, kind) => {
          if (kind === 'videos') {
            return `<video controls preload="metadata" style="max-width: 100%; max-height: 480px; border-radius: 8px; display: block; margin: 8px 0;"><source src="${url}" type="video/mp4">your browser does not support video</video>`;
          }
          return `<a href="${url}" target="_blank"><img src="${url}" style="max-width: 480px; max-height: 480px; border-radius: 6px; display: block; margin: 8px 0;"></a>`;
        }
      );
      // 3) Legacy: pattern "raw/images/<date>/<file>" e "raw/videos/<date>/<file>" — converti a /api/media/
      html = html.replace(
        /(?:<code>|<a[^>]*>|\b)(raw\/(images|videos)\/(\d{4}-\d{2}-\d{2})\/([\w.\-]+\.(?:png|jpg|jpeg|webp|gif|mp4|webm|mov)))(?:<\/code>|<\/a>|\b)/gi,
        (m, full, kind, date, file) => {
          const url = `/api/media/${kind}/${date}/${file}`;
          if (kind === 'videos') {
            return `<video controls preload="metadata" style="max-width: 100%; max-height: 480px; border-radius: 8px; display: block; margin: 8px 0;"><source src="${url}" type="video/mp4">your browser does not support video</video>`;
          }
          return `<a href="${url}" target="_blank"><img src="${url}" alt="${file}" style="max-width: 480px; max-height: 480px; border-radius: 6px; display: block; margin: 8px 0;"></a>`;
        }
      );
      // Fase 22.9+ — Auto-link file paths del workspace/hub (es. files/report.docx)
      // Pattern: <code>(files|data|scripts)/(path)</code>
      html = html.replace(
        /<code>((?:files|data|scripts)\/[^<\s]+)<\/code>/gi,
        (m, filePath) => {
          const ps = jsAttr(filePath);
          return `<a class="file-link" onclick="event.preventDefault(); window.app.openFilePath('${ps}')" href="#" title="Apri ${escText(filePath)}">📄 <span class="text-mono">${escText(filePath)}</span></a>`;
        }
      );
      return html;
    },

    // ===== F-Notify =====

    get notifGrouped() {
      // Raggruppa per source mantenendo ordine d'inserimento (per ts desc)
      const groups = {};
      const order = [];
      for (const n of this.notifications) {
        if (!groups[n.source]) {
          groups[n.source] = [];
          order.push(n.source);
        }
        groups[n.source].push(n);
      }
      return order.map(s => ({ source: s, items: groups[s] }));
    },

    notifSourceIcon(source) {
      return ({
        goal: 'target', kanban: 'kanban-square', routine: 'repeat',
        chat: 'message-square', script: 'terminal', telegram: 'send',
        mcp: 'plug', webapp: 'globe', daemon: 'cpu',
      })[source] || 'bell';
    },

    notifRelativeTs(iso) {
      if (!iso) return '';
      const t = new Date(iso).getTime();
      const d = (Date.now() - t) / 1000;
      if (d < 60) return 'just now';
      if (d < 3600) return `${Math.floor(d / 60)}m`;
      if (d < 86400) return `${Math.floor(d / 3600)}h`;
      return `${Math.floor(d / 86400)}d`;
    },

    _notifFilterParams() {
      const p = new URLSearchParams();
      if (this.notifFilter === 'unread') p.set('unread_only', 'true');
      else if (this.notifFilter === 'errors') p.set('min_severity', '3');
      else if (this.notifFilter === 'action') p.set('category', 'action_needed');
      if (this.notifScopeCurrent && this.workspaceScope && this.workspaceScope !== 'hub') {
        p.set('scope', this.workspaceScope.replace(/^project:/, 'workspace:'));
      }
      p.set('limit', '100');
      return p.toString();
    },

    async loadNotifications() {
      if (this.notifLoading) return;
      this.notifLoading = true;
      try {
        const qs = this._notifFilterParams();
        const r = await fetch(`/api/notifications?${qs}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        this.notifications = data.items || [];
        this.notifUnreadCount = data.unread_count || 0;
        this.refreshIcons();
      } catch (e) {
        console.warn('[notif] load failed:', e);
      } finally {
        this.notifLoading = false;
      }
    },

    connectNotifSSE() {
      if (this.notifSSE) {
        try { this.notifSSE.close(); } catch (e) {}
        this.notifSSE = null;
      }
      try {
        this.notifSSE = new EventSource('/api/notifications/stream');
        this.notifSSE.onmessage = (ev) => {
          if (!ev.data || ev.data.startsWith(':')) return;
          try {
            const n = JSON.parse(ev.data);
            // Snapshot iniziale: array
            if (Array.isArray(n)) {
              // Solo aggiorno unread count, la lista viene popolata da loadNotifications()
              this.notifUnreadCount = n.filter(x => !x.read).length;
              return;
            }
            // Singolo evento: prepend se passa il filter visivo
            if (this._notifPassesFilter(n)) {
              this.notifications = [n, ...this.notifications].slice(0, 200);
            }
            if (!n.read) this.notifUnreadCount += 1;
            this.refreshIcons();
          } catch (e) { /* ignore */ }
        };
        this.notifSSE.onerror = () => {
          // Auto-reconnect dopo 5s
          try { this.notifSSE.close(); } catch (e) {}
          this.notifSSE = null;
          setTimeout(() => this.connectNotifSSE(), 5000);
        };
      } catch (e) {
        console.warn('[notif] SSE connect failed:', e);
      }
    },

    _notifPassesFilter(n) {
      if (this.notifFilter === 'unread' && n.read) return false;
      if (this.notifFilter === 'errors' && (n.severity || 0) < 3) return false;
      if (this.notifFilter === 'action' && n.category !== 'action_needed') return false;
      if (this.notifScopeCurrent && this.workspaceScope && this.workspaceScope !== 'hub') {
        const wantScope = this.workspaceScope.replace(/^project:/, 'workspace:');
        if (n.scope !== wantScope && n.scope !== 'hub') return false;
      }
      return true;
    },

    toggleNotifPanel() {
      this.notifPanelOpen = !this.notifPanelOpen;
      if (this.notifPanelOpen) {
        this.loadNotifications();
        this.$nextTick(() => this.refreshIcons());
      }
    },

    async markNotifRead(id) {
      try {
        await fetch(`/api/notifications/${id}/read`, { method: 'POST' });
        const n = this.notifications.find(x => x.id === id);
        if (n && !n.read) {
          n.read = true;
          this.notifUnreadCount = Math.max(0, this.notifUnreadCount - 1);
        }
      } catch (e) { console.warn('[notif] mark read:', e); }
    },

    async markAllNotifRead() {
      try {
        const qs = (this.notifScopeCurrent && this.workspaceScope && this.workspaceScope !== 'hub')
          ? `?scope=${encodeURIComponent(this.workspaceScope.replace(/^project:/, 'workspace:'))}`
          : '';
        await fetch(`/api/notifications/mark-all-read${qs}`, { method: 'POST' });
        this.notifications.forEach(n => n.read = true);
        this.notifUnreadCount = 0;
      } catch (e) { console.warn('[notif] mark all:', e); }
    },

    async deleteNotif(id) {
      try {
        await fetch(`/api/notifications/${id}`, { method: 'DELETE' });
        const idx = this.notifications.findIndex(x => x.id === id);
        if (idx !== -1) {
          if (!this.notifications[idx].read) {
            this.notifUnreadCount = Math.max(0, this.notifUnreadCount - 1);
          }
          this.notifications.splice(idx, 1);
        }
      } catch (e) { console.warn('[notif] delete:', e); }
    },

    notifClick(n) {
      if (!n.read) this.markNotifRead(n.id);
      const url = n.action && n.action.url;
      if (!url) return;
      // Routing minimo: '/#chat/{id}' → open chat; '/#kanban' → view kanban; '/goals/{scope}/{id}' → goals detail; '/#settings/...' → settings
      if (url.startsWith('/#chat/')) {
        const cid = url.split('/').pop();
        this.view = 'chat';
        if (typeof this.selectConversation === 'function') this.selectConversation(cid);
      } else if (url.startsWith('/#kanban')) {
        this.view = 'kanban';
      } else if (url.startsWith('/goals/')) {
        const parts = url.replace('/goals/', '').split('/');
        this.view = 'goals';
        if (parts[0] && parts[1] && typeof this.openGoalDetail === 'function') {
          this.openGoalDetail(parts[0], parts[1]);
        }
      } else if (url.startsWith('/#settings')) {
        this.view = 'settings';
      } else if (url.startsWith('/#routines')) {
        this.view = 'routines';
      }
    },

    // F-Notify-4 — Activity widget poll
    async loadActivitySummary() {
      try {
        const r = await fetch('/api/activity/summary');
        if (!r.ok) return;
        const data = await r.json();
        data.totalActive = (data.chat_streaming || []).length
                         + (data.routines_running || []).length
                         + (data.goals_running || []).length;
        this.activitySummary = data;
        this.$nextTick(() => this.refreshIcons());
      } catch (e) { /* silent */ }
    },

    startActivityPoll() {
      if (this.activityPollTimer) return;
      this.loadActivitySummary();
      this.activityPollTimer = setInterval(() => this.loadActivitySummary(), 5000);
    },

    stopActivityPoll() {
      if (this.activityPollTimer) {
        clearInterval(this.activityPollTimer);
        this.activityPollTimer = null;
      }
    },

    // WS — Research settings
    async loadResearchSettings() {
      try {
        const r = await fetch('/api/settings/research');
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const cfg = await r.json();
        this.researchSettings.preferred = cfg.preferred || 'duckduckgo';
        this.researchSettings.serpapi_configured = !!cfg.serpapi_configured;
        this.researchSettings.gemini_configured = !!cfg.gemini_configured;
        this.researchSettings.message = '';
        this.$nextTick(() => this.refreshIcons());
      } catch (e) {
        this.researchSettings.message = `Load failed: ${e.message}`;
      }
    },

    async saveResearchSettings() {
      try {
        const r = await fetch('/api/settings/research', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preferred: this.researchSettings.preferred }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        this.researchSettings.message = 'Saved.';
      } catch (e) {
        this.researchSettings.message = `Save failed: ${e.message}`;
      }
    },

    // ===== Fase 12b M-Onb 3 — Settings "Profile" tab =====
    async loadProfileSettings() {
      try {
        const d = await this.fetchJson('/api/settings/defaults');
        this.profileSettings.agentName = (d && d.default_agent_name) || 'Anja';
        this.profileSettings.slug = (d && d.default_user) || (this.hubInfo && this.hubInfo.user) || '';
      } catch (e) { /* defaults non critici */ }
      this.profileSettings.mode = 'hot';
      await this.loadProfileText();
      this.$nextTick(() => this.refreshIcons());
    },

    async loadProfileText() {
      this.profileSettings.message = '';
      const detail = this.profileSettings.mode === 'detail';
      try {
        const r = await fetch(`/api/memory/user?detail=${detail}`);
        if (r.status === 404) { this.profileSettings.content = ''; return; }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        this.profileSettings.content = await r.text();
      } catch (e) {
        this.profileSettings.message = `Load failed: ${e.message}`;
      }
    },

    async saveProfileText() {
      const detail = this.profileSettings.mode === 'detail';
      try {
        const r = await fetch('/api/memory/user', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ detail, content: this.profileSettings.content }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        this.profileSettings.message = 'Profile saved.';
      } catch (e) {
        this.profileSettings.message = `Save failed: ${e.message}`;
      }
    },

    async saveDefaultAgent() {
      try {
        const r = await fetch('/api/settings/defaults', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ default_agent_name: this.profileSettings.agentName.trim() || 'Anja' }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        this.profileSettings.message = 'Agent name saved.';
      } catch (e) {
        this.profileSettings.message = `Save failed: ${e.message}`;
      }
    },

    async testResearch(skill) {
      this.researchSettings.message = `Testing ${skill}…`;
      try {
        const r = await fetch('/api/settings/research/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ skill, query: 'anja personal AI hub' }),
        });
        const data = await r.json();
        if (!data.ok) {
          this.researchSettings.message = `❌ ${skill}: ${data.error}`;
        } else {
          const p = data.preview || {};
          this.researchSettings.message = `✅ ${skill} OK — ${data.count} results. First: "${p.title || ''}" → ${p.url || ''}`;
        }
      } catch (e) {
        this.researchSettings.message = `❌ test request failed: ${e.message}`;
      }
    },

    // F-BackupDR Fase 3b — pannello Backup & DR
    async loadBackupDR() {
      this.backupDR.message = ''; this.backupDR.error = false;
      try {
        const [rb, rs, rv] = await Promise.all([
          fetch('/api/backups'),
          fetch('/api/memory/undo/snapshots?n=30'),
          fetch('/api/version'),
        ]);
        if (!rb.ok) throw new Error(`backups HTTP ${rb.status}`);
        this.backupDR.backups = (await rb.json()).backups || [];
        this.backupDR.snapshots = rs.ok ? ((await rs.json()).snapshots || []) : [];
        this.backupDR.version = rv.ok ? await rv.json() : null;
        this.backupDR.loaded = true;
        this.$nextTick(() => this.refreshIcons());
      } catch (e) {
        this.backupDR.message = `Load failed: ${e.message}`;
        this.backupDR.error = true;
      }
    },

    async runBackupNow() {
      this.backupDR.running = true;
      this.backupDR.message = ''; this.backupDR.error = false;
      try {
        const r = await fetch('/api/backup/run', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: 'manual-ui' }),
        });
        const j = await r.json();
        if (!r.ok || j.ok === false) throw new Error(j.detail || j.error || `HTTP ${r.status}`);
        this.showToast('success', '💾 Backup created', j.archive ? j.archive.split('/').pop() : 'ok');
        await this.loadBackupDR();
      } catch (e) {
        this.backupDR.message = `Backup failed: ${e.message}`;
        this.backupDR.error = true;
      } finally {
        this.backupDR.running = false;
      }
    },

    async copyRestoreCmd(b) {
      const cmd = `python3 anja-hub/webapp/backup.py restore ${b.archive} <target-hub-dir>`;
      try {
        await navigator.clipboard.writeText(cmd);
        this.showToast('success', 'Restore command copied', 'Paste it in a shell on the server');
      } catch (e) {
        this.showToast('error', 'Copy failed', e.message);
      }
    },

    async previewMemoryUndo(sha) {
      if (this.backupDR.previewRef === sha) {           // toggle: nascondi
        this.backupDR.previewRef = '';
        this.backupDR.previewDiff = null;
        this.backupDR.previewChanged = null;
        return;
      }
      this.backupDR.previewBusy = true;
      try {
        const r = await fetch('/api/memory/undo/memory', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ref: sha, preview: true }),
        });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
        this.backupDR.previewRef = sha;
        this.backupDR.previewDiff = j.diff || '';
        this.backupDR.previewChanged = !!j.changed;
      } catch (e) {
        this.showToast('error', 'Preview failed', e.message);
      } finally {
        this.backupDR.previewBusy = false;
      }
    },

    async execMemoryUndo(sha) {
      if (!confirm(`Restore users/*.md to checkpoint ${sha.slice(0, 8)}?\n\nA pre-undo checkpoint is created first: you can go back.`)) return;
      this.backupDR.undoBusy = true;
      try {
        const r = await fetch('/api/memory/undo/memory', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ref: sha }),
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.detail || j.error || `HTTP ${r.status}`);
        this.showToast('success', '🧠 Memory restored', `checkpoint ${sha.slice(0, 8)} · pre-undo ${(j.pre_undo_checkpoint || '').slice(0, 8)}`);
        this.backupDR.previewRef = '';
        this.backupDR.previewDiff = null;
        await this.loadBackupDR();
      } catch (e) {
        this.showToast('error', 'Undo failed', e.message);
      } finally {
        this.backupDR.undoBusy = false;
      }
    },

    async loadCardsUndoPreview() {
      this.backupDR.cardsBusy = true;
      try {
        const r = await fetch('/api/memory/undo/cards', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dry_run: true }),
        });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
        this.backupDR.cardsPreview = j;
      } catch (e) {
        this.showToast('error', 'Card preview failed', e.message);
      } finally {
        this.backupDR.cardsBusy = false;
      }
    },

    async execCardsUndo() {
      const n = this.backupDR.cardsPreview?.count || 0;
      if (!n || !confirm(`Archive ${n} autonomous cards? (reversible: status='archived', never deleted)`)) return;
      this.backupDR.cardsBusy = true;
      try {
        const r = await fetch('/api/memory/undo/cards', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dry_run: false }),
        });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
        this.showToast('success', '📦 Cards archived', `${j.count} cards (reversible)`);
        this.backupDR.cardsPreview = null;
      } catch (e) {
        this.showToast('error', 'Archive failed', e.message);
      } finally {
        this.backupDR.cardsBusy = false;
      }
    },

    async runMigrate() {
      const v = this.backupDR.version || {};
      if (!confirm(`Apply the hub data update ${v.hub_version || '?'} → ${v.code_version}?\n\nA pre-update backup is created first.`)) return;
      this.backupDR.migrateBusy = true;
      try {
        const r = await fetch('/api/update/migrate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.detail || j.error || `HTTP ${r.status}`);
        const n = (j.migrations?.applied || []).length;
        this.showToast('success', '⬆️ Hub updated', `${j.from || '—'} → ${j.to} · ${n} migrations · pre-update backup ok`);
        await this.loadBackupDR();
      } catch (e) {
        this.showToast('error', 'Migrate failed', e.message);
      } finally {
        this.backupDR.migrateBusy = false;
      }
    },

    // F-Notify-6 — Settings card
    async loadNotifSettings() {
      try {
        const r = await fetch('/api/settings/notifications');
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const cfg = await r.json();
        this.notifSettings.sources = cfg.sources || this.notifSettings.sources;
        this.notifSettings.min_severity = cfg.min_severity ?? 0;
        this.notifSettings.mute_telegram_echo = !!cfg.mute_telegram_echo;
        this.notifSettings.auto_cleanup_days = cfg.auto_cleanup_days ?? 30;
        this.notifSettings.message = '';
        this.$nextTick(() => this.refreshIcons());
      } catch (e) {
        this.notifSettings.message = `Load failed: ${e.message}`;
      }
    },

    async saveNotifSettings() {
      this.notifSettings.saving = true;
      this.notifSettings.message = '';
      try {
        const payload = {
          sources: this.notifSettings.sources,
          min_severity: this.notifSettings.min_severity,
          mute_telegram_echo: this.notifSettings.mute_telegram_echo,
          auto_cleanup_days: this.notifSettings.auto_cleanup_days,
        };
        const r = await fetch('/api/settings/notifications', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        this.notifSettings.message = 'Saved.';
      } catch (e) {
        this.notifSettings.message = `Save failed: ${e.message}`;
      } finally {
        this.notifSettings.saving = false;
      }
    },

    async testNotif() {
      try {
        const r = await fetch('/api/notifications/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: 'Test notification', body: 'Triggered from Settings',
            category: 'info', scope: this.workspaceScope === 'hub' ? 'hub' :
                                     (this.workspaceScope || 'hub').replace(/^project:/, 'workspace:'),
          }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        this.notifSettings.message = 'Test notification sent — check the Bell.';
      } catch (e) {
        this.notifSettings.message = `Test failed: ${e.message}`;
      }
    },

    // Fase 22.9+ — Click su file path in chat → naviga al file
    openFilePath(filePath) {
      const parts = filePath.split('/');
      const filename = parts.pop();
      const cwd = parts.join('/');
      // Se siamo in project scope, vai a project-files; altrimenti hub-files
      if (this.isProjectScope && this.currentProjectScopeName) {
        this.projectFiles.project = this.currentProjectScopeName;
        this.view = 'project-files';
        this.projectFiles.cwd = cwd;
        this.loadProjectFilesDir(cwd).then(() => {
          if (typeof this.openProjectFile === 'function') this.openProjectFile(filename);
        });
      } else {
        this.view = 'hub-files';
        this.hubFiles.cwd = cwd;
        this.loadHubFilesDir(cwd).then(() => this.openHubFile(filename));
      }
      this.refreshIcons();
    },
  };
}
