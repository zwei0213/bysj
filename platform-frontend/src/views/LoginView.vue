<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()

const mode = ref('login') // login | register
const loginForm = ref({ username: '', password: '' })
const regForm = ref({
  username: '',
  display_name: '',
  email: '',
  password: '',
  confirm_password: '',
  role: 'student',
})
const errorMsg = ref('')
const loading = ref(false)

function navigateByRole(user) {
  if (user.role === 'admin') router.push('/admin')
  else if (user.role === 'teacher' && user.teacher_approved) router.push('/teacher')
  else router.push('/student')
}

async function handleLogin() {
  errorMsg.value = ''
  if (!loginForm.value.username.trim() || !loginForm.value.password) {
    errorMsg.value = '请输入登录账号与密码'
    return
  }
  loading.value = true
  try {
    const user = await auth.login(loginForm.value.username.trim(), loginForm.value.password)
    navigateByRole(user)
  } catch (err) {
    errorMsg.value = err.message || '登录认证失败'
  } finally {
    loading.value = false
  }
}

async function handleRegister() {
  errorMsg.value = ''
  if (!regForm.value.username.trim() || !regForm.value.display_name.trim()) {
    errorMsg.value = '请填写登录账号与真实姓名'
    return
  }
  if (regForm.value.password.length < 8) {
    errorMsg.value = '密码长度至少为 8 位字符'
    return
  }
  if (regForm.value.password !== regForm.value.confirm_password) {
    errorMsg.value = '两次输入的密码不一致'
    return
  }
  loading.value = true
  try {
    const user = await auth.register({
      username: regForm.value.username.trim(),
      display_name: regForm.value.display_name.trim(),
      email: regForm.value.email.trim(),
      password: regForm.value.password,
      role: regForm.value.role,
    })
    navigateByRole(user)
  } catch (err) {
    errorMsg.value = err.message || '账号注册失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="auth-canvas">
    <article class="doc-page auth-doc">
      <header class="doc-header">
        <div class="section-label">知心智学 · 智能教学空间</div>
        <h1 class="doc-display" style="margin-top: 8px;">知心智学 · 智能学习空间</h1>
        <p class="doc-subtitle">
          面向深度学习阅读与知识沉淀的在线课程工作区：融合授课视频语音转写、秒级章节导航、课程专属文献溯源智能助教与薄弱知识点靶向诊断。
        </p>
      </header>

      <section class="doc-section">
        <div class="tabs-bar">
          <button
            type="button"
            class="tab-pill"
            :class="{ active: mode === 'login' }"
            @click="mode = 'login'; errorMsg = ''"
          >
            账号登录
          </button>
          <button
            type="button"
            class="tab-pill"
            :class="{ active: mode === 'register' }"
            @click="mode = 'register'; errorMsg = ''"
          >
            新用户注册
          </button>
        </div>

        <div class="card surface-white auth-sheet">
          <form v-if="mode === 'login'" @submit.prevent="handleLogin" class="form-stack">
            <div class="field">
              <label class="label-text" for="login-username">登录账号</label>
              <input
                id="login-username"
                v-model="loginForm.username"
                class="input"
                autocomplete="username"
                placeholder="请输入登录账号（支持字母、数字或学工号）"
              />
            </div>

            <div class="field">
              <label class="label-text" for="login-password">密码</label>
              <input
                id="login-password"
                v-model="loginForm.password"
                type="password"
                class="input"
                autocomplete="current-password"
                placeholder="请输入登录密码"
              />
            </div>

            <div v-if="errorMsg" class="callout callout-danger" role="alert">
              <span class="callout-icon" aria-hidden="true">!</span>
              <span>{{ errorMsg }}</span>
            </div>

            <div class="form-actions">
              <button type="submit" class="btn btn-primary" :disabled="loading">
                {{ loading ? '认证中...' : '进入工作区' }}
              </button>
            </div>
          </form>

          <form v-else @submit.prevent="handleRegister" class="form-stack">
            <div class="field">
              <label class="label-text" for="reg-role">注册身份</label>
              <select id="reg-role" v-model="regForm.role" class="select">
                <option value="student">学员账号（注册后立即开通选课学习）</option>
                <option value="teacher">授课教师入驻（提交后由平台管理员审核开通）</option>
              </select>
            </div>

            <div class="field">
              <label class="label-text" for="reg-username">登录账号</label>
              <input id="reg-username" v-model="regForm.username" class="input" placeholder="请输入登录账号（支持字母、数字或学工号）" />
            </div>

            <div class="field">
              <label class="label-text" for="reg-name">真实姓名 / 昵称</label>
              <input id="reg-name" v-model="regForm.display_name" class="input" placeholder="请输入真实姓名或昵称" />
            </div>

            <div class="field">
              <label class="label-text" for="reg-email">联系邮箱</label>
              <input id="reg-email" v-model="regForm.email" type="email" class="input" placeholder="请输入常用联系邮箱" />
            </div>

            <div class="field">
              <label class="label-text" for="reg-password">设置密码（不少于 8 位）</label>
              <input id="reg-password" v-model="regForm.password" type="password" class="input" placeholder="请输入至少 8 位密码" />
            </div>

            <div class="field">
              <label class="label-text" for="reg-confirm">确认密码</label>
              <input id="reg-confirm" v-model="regForm.confirm_password" type="password" class="input" placeholder="请再次输入密码" />
            </div>

            <div v-if="errorMsg" class="callout callout-danger" role="alert">
              <span class="callout-icon" aria-hidden="true">!</span>
              <span>{{ errorMsg }}</span>
            </div>

            <div class="form-actions">
              <button type="submit" class="btn btn-primary" :disabled="loading">
                {{ loading ? '创建中...' : '创建账号并进入' }}
              </button>
            </div>
          </form>
        </div>
      </section>

      <section class="doc-section">
        <div class="callout">
          <span class="callout-icon" aria-hidden="true">
            <svg class="icon-16" viewBox="0 0 16 16"><path d="M3 2.5h10v11H3z"/><path d="M5.5 6h5M5.5 9h5"/></svg>
          </span>
          <div>
            <strong>文档化学习流：</strong>所有智能生成的视频章节提纲与课程试题均经授课教师校准发布后开放阅读；每次智能助教答复均附带可跳转的讲义或视频秒级锚点引用。
          </div>
        </div>
      </section>
    </article>
  </main>
</template>

<style scoped>
.auth-canvas {
  min-height: 100vh;
  background-color: var(--color-paper);
  padding: 0 clamp(24px, 6vw, 96px);
}

.auth-doc {
  padding-top: var(--spacing-64);
}

.auth-sheet {
  padding: var(--spacing-24);
}

.form-stack {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-16);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-4);
}

.form-actions {
  display: flex;
  justify-content: flex-start;
  padding-top: var(--spacing-4);
}
</style>
