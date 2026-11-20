import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
  },
  {
    path: '/',
    redirect: () => {
      const auth = useAuthStore()
      if (!auth.isAuthenticated) return '/login'
      if (auth.role === 'teacher') return '/teacher'
      if (auth.role === 'admin') return '/admin'
      return '/student'
    },
  },
  {
    path: '/student',
    name: 'student-hub',
    component: () => import('../views/student/StudentHubView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/student/courses/:courseId',
    name: 'course-study',
    component: () => import('../views/student/CourseStudyView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/student/wrong-book',
    name: 'wrong-book',
    component: () => import('../views/student/WrongBookView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/teacher',
    name: 'teacher-studio',
    component: () => import('../views/teacher/TeacherStudioView.vue'),
    meta: { requiresAuth: true, roles: ['teacher', 'admin'] },
  },
  {
    path: '/admin',
    name: 'admin-console',
    component: () => import('../views/admin/AdminConsoleView.vue'),
    meta: { requiresAuth: true, roles: ['admin'] },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'login' }
  }
  if (to.meta.roles && !to.meta.roles.includes(auth.role)) {
    return '/'
  }
  return true
})

export default router
