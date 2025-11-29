import os
import io
import json
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from google import genai
from google.genai.types import Part
from PIL import Image
from dotenv import load_dotenv
from pydantic import BaseModel
import asyncio

load_dotenv()

def load_prompt_file(filename: str) -> str:
    try:
        file_path = os.path.join("prompts", filename)
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
        
    except FileNotFoundError:
        raise RuntimeError(f"Required Prompt file not found: {filename}")    

API_KEY = os.getenv("GEMINI_KEY") 
MODEL = os.getenv("MODEL")

app = FastAPI(title="learning app") 

try:
    if not API_KEY:
        raise ValueError("NO API KEY SET")
    
    client = genai.Client(api_key=API_KEY)
    aclient = genai.Client(api_key=API_KEY).aio

except Exception as e:
    print(f"Exceotion: {e}")
    client = None

try: 
    QUIZ_SYSTEM_PROMPT=load_prompt_file("quiz_prompt.md")
    NOTES_SYSTEM_PROMPT=load_prompt_file("notes_prompt.md")
    QUIZ_TASK_PROMPT=load_prompt_file("quiz_task_prompt.md")
    NOTES_TASK_PROMPT=load_prompt_file("notes_task_prompt.md")
    JSON_SCHEMA = '{"quiz_title": "...", "questions": [{"question": "...", "options": [{"text": "...", "is_correct": true/false}], "explanation": "..."}]}'

except RuntimeError as e:
    print(f"Fatal Error Prompt loading Failed: {e}")


@app.post("/proccess-batch")
async def proccess_batch(
    files: list[UploadFile] = File(..., description="Image or PDF file."),
    mode: str = Form(..., description="Desired output: 'quiz' or 'notes'."),
    prompt_instruction: str = Form(...) # additional from app
    ):

    if client is None or aclient is None:
        raise HTTPException(status_code=503, detail="Gemini Client not initialized. Check API Key.")
    
    tasks = []

    for uploaded_file in files: 
        file_bytes = await uploaded_file.read()
        file_type = uploaded_file.content_type

        if file_type.startswith("image/"):
            file_part = Image.open(io.BytesIO(file_bytes))
        elif file_type == "application/pdf":
            file_part = Part.from_bytes(data=file_bytes, mime_type="application/pdf")
        else:
            print(f"skipping unsupported file type: {file_type}")
            continue
        try: 
            system_prompt, task_prompt, file_type_setting = get_prompts_and_config(mode, prompt_instruction)
        except HTTPException as e:
            raise e
        
        task = proccess_single_document(aclient, file_part, system_prompt, task_prompt, file_type_setting)
        tasks.append(task)

    if not tasks:
        raise HTTPException(status_code=400, detail="No valid files were submitted for processing.")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    final_output = []

    for result in results:
        if isinstance(result, Exception):
            error_detail = f"Processing failed for a file: {str(result)}"
            print(error_detail)
            final_output.append({"status": "error", "message": error_detail})
        else:
            final_output.append({"status": "success", "data": result})

    return JSONResponse(content=final_output, status_code=200)



def get_prompts_and_config(mode: str, instruction: str):
    if mode == "quiz":
        system_prompt = QUIZ_SYSTEM_PROMPT
        task_prompt = QUIZ_TASK_PROMPT.format(
            user_instruction=instruction,
            json_schema=JSON_SCHEMA
        )
        file_type_setting = "application/json"

    elif mode == "notes":
        system_prompt = NOTES_SYSTEM_PROMPT
        task_prompt = NOTES_TASK_PROMPT.format(
            user_instruction=instruction
        )
        file_type_setting = "text/plain"
    else:
        raise HTTPException(status_code=400, detail=f"Unsuported Generation MOde: {mode}")
    
    return system_prompt, task_prompt, file_type_setting

async def proccess_single_document(aclient, file_part, system_prompt, task_prompt, file_type_setting):
    contents = [
        {"text": system_prompt},
        file_part,
        {"text": task_prompt}
    ]

    response = await aclient.models.generate_content(
        model=MODEL or "gemini-2.5-flash",
        contents=contents,
        config=genai.types.GenerateContentConfig(response_mime_type=file_type_setting)
    )

    if file_type_setting == 'application/json':
        json_text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(json_text)
    else:
        return {"result": response.text}
    

