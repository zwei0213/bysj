import { apiRequest, apiStreamRequest } from '../client'

export const studentApi = {
  fetchDashboard() {
    return apiRequest('/api/v1/users/me/dashboard')
  },
  fetchCourses(paramsOrKeyword = '') {
    const params = new URLSearchParams()
    if (typeof paramsOrKeyword === 'string') {
      if (paramsOrKeyword.trim()) params.set('keyword', paramsOrKeyword.trim())
    } else if (paramsOrKeyword && typeof paramsOrKeyword === 'object') {
      if (paramsOrKeyword.keyword?.trim()) params.set('keyword', paramsOrKeyword.keyword.trim())
      if (paramsOrKeyword.category?.trim()) params.set('category', paramsOrKeyword.category.trim())
      if (paramsOrKeyword.featuredOnly) params.set('featured_only', 'true')
      if (paramsOrKeyword.page) params.set('page', String(paramsOrKeyword.page))
      if (paramsOrKeyword.pageSize) params.set('page_size', String(paramsOrKeyword.pageSize))
    }
    const qs = params.toString()
    return apiRequest(`/api/v1/courses${qs ? `?${qs}` : ''}`)
  },
  fetchCourseDetail(courseId) {
    return apiRequest(`/api/v1/courses/${encodeURIComponent(courseId)}`)
  },
  enrollCourse(courseId) {
    return apiRequest(`/api/v1/courses/${encodeURIComponent(courseId)}/enrollments`, { method: 'POST' })
  },
  fetchVideoPlayback(videoId) {
    return apiRequest(`/api/v1/videos/${encodeURIComponent(videoId)}/playback`)
  },
  reportLearningEvent(payload) {
    return apiRequest('/api/v1/users/me/learning-events', {
      method: 'POST',
      body: payload,
    })
  },
  fetchNotes(videoId, { bookmarksOnly = false, courseId = '' } = {}) {
    const params = new URLSearchParams()
    if (videoId) params.set('video_id', videoId)
    if (courseId) params.set('course_id', courseId)
    if (bookmarksOnly) params.set('bookmarks_only', 'true')
    return apiRequest(`/api/v1/users/me/notes?${params.toString()}`)
  },
  createNote(payload) {
    return apiRequest('/api/v1/users/me/notes', {
      method: 'POST',
      body: payload,
    })
  },
  updateNote(noteId, payload) {
    return apiRequest(`/api/v1/users/me/notes/${encodeURIComponent(noteId)}`, {
      method: 'PATCH',
      body: payload,
    })
  },
  deleteNote(noteId) {
    return apiRequest(`/api/v1/users/me/notes/${encodeURIComponent(noteId)}`, { method: 'DELETE' })
  },
  fetchTutorThreads(courseId) {
    return apiRequest(`/api/v1/courses/${encodeURIComponent(courseId)}/tutor-threads`)
  },
  createThreadAndAsk(courseId, payload) {
    return apiRequest(`/api/v1/courses/${encodeURIComponent(courseId)}/tutor-threads`, {
      method: 'POST',
      body: payload,
    })
  },
  createThreadAndAskStream(courseId, payload, onEvent) {
    return apiStreamRequest(`/api/v1/courses/${encodeURIComponent(courseId)}/tutor-threads/stream`, {
      method: 'POST',
      body: payload,
      onEvent,
    })
  },
  fetchThreadMessages(threadId) {
    return apiRequest(`/api/v1/tutor-threads/${encodeURIComponent(threadId)}/messages`)
  },
  appendThreadMessage(threadId, payload) {
    return apiRequest(`/api/v1/tutor-threads/${encodeURIComponent(threadId)}/messages`, {
      method: 'POST',
      body: payload,
    })
  },
  appendThreadMessageStream(threadId, payload, onEvent) {
    return apiStreamRequest(`/api/v1/tutor-threads/${encodeURIComponent(threadId)}/messages/stream`, {
      method: 'POST',
      body: payload,
      onEvent,
    })
  },
  submitMessageFeedback(messageId, payload) {
    return apiRequest(`/api/v1/tutor-messages/${encodeURIComponent(messageId)}/feedback`, {
      method: 'POST',
      body: payload,
    })
  },
  fetchBookmarkedMessages(courseId = '') {
    const qs = courseId ? `?course_id=${encodeURIComponent(courseId)}` : ''
    return apiRequest(`/api/v1/users/me/bookmarked-messages${qs}`)
  },
  startQuizSession(courseId, payload) {
    return apiRequest(`/api/v1/courses/${encodeURIComponent(courseId)}/quiz-sessions`, {
      method: 'POST',
      body: payload,
    })
  },
  submitQuizAnswers(sessionId, answers) {
    return apiRequest(`/api/v1/quiz-sessions/${encodeURIComponent(sessionId)}/submissions`, {
      method: 'POST',
      body: { answers },
    })
  },
  fetchWrongQuestions({ courseId = '', knowledgePoint = '', includeMastered = true } = {}) {
    const params = new URLSearchParams()
    if (courseId.trim()) params.set('course_id', courseId.trim())
    if (knowledgePoint.trim()) params.set('knowledge_point', knowledgePoint.trim())
    if (!includeMastered) params.set('include_mastered', 'false')
    const qs = params.toString()
    return apiRequest(`/api/v1/users/me/wrong-questions${qs ? `?${qs}` : ''}`)
  },
  patchWrongQuestion(recordId, payload) {
    return apiRequest(`/api/v1/users/me/wrong-questions/${encodeURIComponent(recordId)}`, {
      method: 'PATCH',
      body: payload,
    })
  },
}
