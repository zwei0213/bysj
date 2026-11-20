import { apiRequest, apiUploadWithProgress, apiPutChunkWithProgress } from '../client'

/**
 * 自适应文件字段名上传（兼容后端 File 参数命名为 file / cover_file / xlsx_file / chunk_file 等变体）。
 */
async function uploadMultipartWithFieldFallback(path, file, primaryField, extraFields = {}, onProgress = null, method = 'POST') {
  const buildForm = (fieldName) => {
    const fd = new FormData()
    fd.append(fieldName, file, file.name || 'upload.bin')
    for (const [k, v] of Object.entries(extraFields)) {
      if (v !== undefined && v !== null && v !== '') {
        fd.append(k, String(v))
      }
    }
    return fd
  }

  try {
    return await apiUploadWithProgress(path, buildForm(primaryField), onProgress, { method })
  } catch (err) {
    if (err?.status === 422 && Array.isArray(err.details)) {
      const missingDetail = err.details.find((d) => String(d.field || '').startsWith('body.'))
      const expectedField = missingDetail ? String(missingDetail.field).replace(/^body\./, '') : ''
      if (expectedField && expectedField !== primaryField) {
        return apiUploadWithProgress(path, buildForm(expectedField), onProgress, { method })
      }
    }
    throw err
  }
}

export const teacherApi = {
  fetchCourses() {
    return apiRequest('/api/v1/teacher/courses')
  },
  createCourse(payload) {
    return apiRequest('/api/v1/teacher/courses', {
      method: 'POST',
      body: payload,
    })
  },
  updateCourse(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}`, {
      method: 'PATCH',
      body: payload,
    })
  },
  uploadCourseCover(courseId, file, onProgress) {
    return uploadMultipartWithFieldFallback(
      `/api/v1/teacher/courses/${encodeURIComponent(courseId)}/cover-uploads`,
      file,
      'cover_file',
      {},
      onProgress,
      'POST'
    )
  },
  createClassGroup(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/classes`, {
      method: 'POST',
      body: payload,
    })
  },
  fetchCourseStudents(courseId, classId = '') {
    const qs = classId ? `?class_id=${encodeURIComponent(classId)}` : ''
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/students${qs}`)
  },
  importCourseStudentsXlsx(courseId, file, extraFields = {}, onProgress = null) {
    return uploadMultipartWithFieldFallback(
      `/api/v1/teacher/courses/${encodeURIComponent(courseId)}/students/xlsx-import`,
      file,
      'file',
      extraFields,
      onProgress,
      'POST'
    )
  },
  async batchAddCourseStudents(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/students/import`, {
      method: 'POST',
      body: {
        class_id: payload.class_id || null,
        group_name: payload.group_name || '',
        usernames_or_csv: payload.usernames_or_csv ?? payload.csv_text ?? payload.usernames ?? '',
      },
    })
  },
  updateEnrollment(enrollmentId, payload) {
    return apiRequest(`/api/v1/teacher/enrollments/${encodeURIComponent(enrollmentId)}`, {
      method: 'PATCH',
      body: payload,
    })
  },
  removeEnrollment(enrollmentId) {
    return apiRequest(`/api/v1/teacher/enrollments/${encodeURIComponent(enrollmentId)}`, {
      method: 'DELETE',
    })
  },
  uploadVideo(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/videos`, {
      method: 'POST',
      body: payload,
    })
  },
  uploadVideoFile(courseId, formData, onProgress) {
    return apiUploadWithProgress(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/video-uploads`, formData, onProgress)
  },
  initVideoChunkUpload(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/video-uploads/init`, {
      method: 'POST',
      body: payload,
    })
  },
  async uploadVideoChunk(courseId, uploadId, chunkIdx, chunkBlob, onProgress) {
    const paths = [
      `/api/v1/teacher/video-uploads/${encodeURIComponent(uploadId)}/chunks/${chunkIdx}`,
      `/api/v1/teacher/courses/${encodeURIComponent(courseId)}/video-uploads/${encodeURIComponent(uploadId)}/chunks/${chunkIdx}`,
    ]
    let lastErr = null
    for (const path of paths) {
      try {
        return await apiPutChunkWithProgress(path, chunkBlob, onProgress)
      } catch (err) {
        if (err?.status === 404 || err?.status === 405) {
          lastErr = err
          continue
        }
        if (err?.status === 422 || err?.status === 400) {
          const chunkFile = new File([chunkBlob], `chunk_${chunkIdx}.part`, { type: 'application/octet-stream' })
          return uploadMultipartWithFieldFallback(path, chunkFile, 'chunk_file', { chunk_index: chunkIdx }, onProgress, 'PUT')
        }
        throw err
      }
    }
    throw lastErr || new Error('分片上传端点不可达')
  },
  async completeVideoChunkUpload(courseId, uploadId, payload = {}) {
    const paths = [
      `/api/v1/teacher/video-uploads/${encodeURIComponent(uploadId)}/complete`,
      `/api/v1/teacher/courses/${encodeURIComponent(courseId)}/video-uploads/${encodeURIComponent(uploadId)}/complete`,
    ]
    let lastErr = null
    for (const path of paths) {
      try {
        return await apiRequest(path, { method: 'POST', body: payload })
      } catch (err) {
        if (err?.status === 404 || err?.status === 405) {
          lastErr = err
          continue
        }
        throw err
      }
    }
    throw lastErr || new Error('分片合并端点不可达')
  },
  syncVideoJob(videoId) {
    return apiRequest(`/api/v1/teacher/videos/${encodeURIComponent(videoId)}/job-sync`, {
      method: 'POST',
    })
  },
  uploadDocument(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/documents`, {
      method: 'POST',
      body: payload,
    })
  },
  uploadDocumentFile(courseId, formData, onProgress) {
    return apiUploadWithProgress(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/document-uploads`, formData, onProgress)
  },
  deleteDocument(docId) {
    return apiRequest(`/api/v1/teacher/documents/${encodeURIComponent(docId)}`, {
      method: 'DELETE',
    })
  },
  fetchOutlineVersions(videoId) {
    return apiRequest(`/api/v1/teacher/videos/${encodeURIComponent(videoId)}/outline-versions`)
  },
  saveOutlineVersion(videoId, payload) {
    return apiRequest(`/api/v1/teacher/videos/${encodeURIComponent(videoId)}/outline-versions`, {
      method: 'POST',
      body: payload,
    })
  },
  publishOutlineVersion(videoId, versionId) {
    return apiRequest(
      `/api/v1/teacher/videos/${encodeURIComponent(videoId)}/outline-versions/${encodeURIComponent(versionId)}/publications`,
      { method: 'POST' }
    )
  },
  regenerateChapter(videoId, payload) {
    return apiRequest(`/api/v1/teacher/videos/${encodeURIComponent(videoId)}/chapter-regenerations`, {
      method: 'POST',
      body: payload,
    })
  },
  submitQualityFeedback(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/quality-feedback`, {
      method: 'POST',
      body: payload,
    })
  },
  fetchQuestions(courseId, { reviewStatus = '', knowledgePoint = '' } = {}) {
    const params = new URLSearchParams()
    if (reviewStatus) params.set('review_status', reviewStatus)
    if (knowledgePoint.trim()) params.set('knowledge_point', knowledgePoint.trim())
    const qs = params.toString()
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/questions${qs ? `?${qs}` : ''}`)
  },
  createQuestion(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/questions`, {
      method: 'POST',
      body: payload,
    })
  },
  generateQuestions(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/question-generations`, {
      method: 'POST',
      body: payload,
    })
  },
  reviewQuestion(questionId, payload) {
    return apiRequest(`/api/v1/teacher/questions/${encodeURIComponent(questionId)}`, {
      method: 'PATCH',
      body: payload,
    })
  },
  deleteQuestion(questionId) {
    return apiRequest(`/api/v1/teacher/questions/${encodeURIComponent(questionId)}`, {
      method: 'DELETE',
    })
  },
  async fetchExams(courseId) {
    try {
      return await apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/exams`)
    } catch (err) {
      if (err?.status === 404 || err?.status === 405) {
        const detail = await apiRequest(`/api/v1/courses/${encodeURIComponent(courseId)}`)
        return { items: detail?.exams || [] }
      }
      throw err
    }
  },
  publishExam(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/exams`, {
      method: 'POST',
      body: payload,
    })
  },
  deleteExam(examId) {
    return apiRequest(`/api/v1/teacher/exams/${encodeURIComponent(examId)}`, {
      method: 'DELETE',
    })
  },
  fetchUnansweredInbox(courseId) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/unanswered-questions`)
  },
  resolveInboxQuestion(itemId, payload) {
    return apiRequest(`/api/v1/teacher/unanswered-questions/${encodeURIComponent(itemId)}/resolutions`, {
      method: 'POST',
      body: payload,
    })
  },
  fetchTutorConfig(courseId) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/tutor-config`)
  },
  saveTutorConfig(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/tutor-config`, {
      method: 'PUT',
      body: payload,
    })
  },
  fetchFaqs(courseId) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/faqs`)
  },
  createFaq(courseId, payload) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/faqs`, {
      method: 'POST',
      body: payload,
    })
  },
  updateFaq(faqId, payload) {
    return apiRequest(`/api/v1/teacher/faqs/${encodeURIComponent(faqId)}`, {
      method: 'PATCH',
      body: payload,
    })
  },
  deleteFaq(faqId) {
    return apiRequest(`/api/v1/teacher/faqs/${encodeURIComponent(faqId)}`, {
      method: 'DELETE',
    })
  },
  fetchAnalytics(courseId) {
    return apiRequest(`/api/v1/teacher/courses/${encodeURIComponent(courseId)}/analytics`)
  },
}
