from fastapi import APIRouter, Request
from app.core.schemas import ok_response
from app.schemas.business import DocumentUpsertDTO
from app.services.vector_store import knowledge_store

router = APIRouter(tags=["KnowledgeBases"])


@router.put("/knowledge-bases/{course_id}/documents/{document_id}")
async def upsert_knowledge_document(
    course_id: str,
    document_id: str,
    body: DocumentUpsertDTO,
    request: Request,
):
    res = await knowledge_store.upsert_document_async(
        course_id,
        document_id,
        title=body.title,
        source_type=str(body.source_type),
        content=body.content,
        metadata=body.metadata,
    )
    return ok_response(res, request)


@router.delete("/knowledge-bases/{course_id}/documents/{document_id}")
async def delete_knowledge_document(course_id: str, document_id: str, request: Request):
    res = knowledge_store.delete_document(course_id, document_id)
    return ok_response(res, request)
