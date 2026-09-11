#!/usr/bin/env python3
# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import os
import sys
import json
import urllib.request
import urllib.error

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma4:26b"

# Exclude directories in-place to prevent os.walk from entering them
EXCLUDE_DIRS = {".git", ".github", ".venv", "test_venv", "__pycache__", "assets", "Resources", "scratch", "memory"}

def load_review_context(filepath, project_root, task_type="1"):
    """Dynamically load AGENTS.md, the Code Review SKILL.md, and relevant subsystem skills."""
    context = []
    
    # 1. Load and prune AGENTS.md (extract only conventions and known bugs to save context tokens)
    agents_md_path = os.path.join(project_root, "AGENTS.md")
    if os.path.exists(agents_md_path):
        with open(agents_md_path, "r", encoding="utf-8") as f:
            agents_content = f.read()
            
        # Parse only the Code Conventions and Known Bugs sections
        lines = agents_content.splitlines()
        relevant_lines = []
        capture = False
        for line in lines:
            if line.startswith("## Code Conventions"):
                capture = True
            elif line.startswith("## Agent Skills"):
                capture = False
            elif line.startswith("## Known Bugs"):
                capture = True
            elif line.startswith("## Active Task Files"):
                capture = False
            if capture:
                relevant_lines.append(line)
        
        pruned_rules = "\n".join(relevant_lines)
        context.append("=== Core Repository Conventions & Known Bugs (from AGENTS.md) ===\n" + pruned_rules)
            
    if task_type == "1":
        # 2. Load the Master Code Review Process Skill
        review_skill_path = os.path.join(project_root, ".agents", "skills", "fld_code_review", "SKILL.md")
        if os.path.exists(review_skill_path):
            with open(review_skill_path, "r", encoding="utf-8") as f:
                context.append("=== Code Review Process Checklists (from fld_code_review/SKILL.md) ===\n" + f.read())
                
        # 3. Determine and load matching specific skills based on the path of the file under review
        skills_dir = os.path.join(project_root, ".agents", "skills")
        matched_skills = []
        
        filepath_lower = filepath.lower()
        if "input" in filepath_lower or "fld_base" in filepath_lower:
            matched_skills.extend(["fld_qt_input_architecture"])
        elif "freecad.fields.commands" in filepath_lower:
            matched_skills.extend(["fld_command_group"])
        elif "freecad.fields.tools" in filepath_lower:
            if "primitive" in filepath_lower:
                matched_skills.extend(["fld_primitive_tool", "fld_primitive_flow"])
            else:
                matched_skills.extend(["fld_tool_refactor_pattern", "fld_rclick_repeat"])
        elif "sdf" in filepath_lower:
            if "sdf_field" in filepath_lower or "sdf_composer" in filepath_lower:
                matched_skills.extend(["fld_sdf_boolean", "fld_boolean_architecture"])
            elif "octree" in filepath_lower:
                matched_skills.extend(["fld_sdf_octree"])
            elif "slicer" in filepath_lower:
                matched_skills.extend(["fld_sdf_slicer"])
        elif "render" in filepath_lower:
            matched_skills.extend(["fld_renderer_architecture", "fld_voxel_pipeline"])
        elif "mesher" in filepath_lower or "baker" in filepath_lower:
            matched_skills.extend(["fld_mesher_architecture", "fld_sdf_octree"])

        for skill_name in set(matched_skills):
            skill_path = os.path.join(skills_dir, skill_name, "SKILL.md")
            if os.path.exists(skill_path):
                with open(skill_path, "r", encoding="utf-8") as f:
                    context.append(f"=== Subsystem Skill Guide: {skill_name} ===\n" + f.read())
                    
        # Add final instructional system prompt (LLM-optimized, token-minimized format)
        final_instruction = """
Using the rules, guidelines, and checklists provided in the context blocks above, perform a token-minimized, extremely succinct code review on the code provided in the user message.
Format the output specifically for ingestion by OTHER LLMs to minimize context size.
Do not use any conversational filler, greetings, or explanations. Use a compact, dense format:

- [RULE_ID] Line [Num]: [Brief issue description] -> Fix: [Brief change]
- [RULE_ID] Line [Num]: [Brief issue description] -> Fix: [Brief change]

Only list direct violations. If a file has no violations, output: "No violations".
"""
        context.append(final_instruction)

    elif task_type == "2":
        # File Summarization
        final_instruction = """
Provide a clear, high-level, and concise functional summary of the provided Python file.
Focus on:
1. The primary responsibility of the classes and functions.
2. Major entry points and how other modules invoke this code.
Keep the output extremely clean, structured, and token-efficient.
"""
        context.append(final_instruction)

    elif task_type == "3":
        # Integration Analysis
        final_instruction = """
You are an expert software architect.
Analyze the relationships, dependencies, and integration patterns between the provided files.
Explain:
1. How they coordinate, communicate, and pass data/events.
2. The hierarchy and flow of control between them.
3. Any coupling or integration risks.
Keep your analysis structured, clear, and highly focused.
"""
        context.append(final_instruction)
        
    return "\n\n".join(context)

def fetch_available_models():
    """Fetch list of models from local Ollama instance."""
    try:
        req = urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags")
        data = json.loads(req.read().decode("utf-8"))
        return [model["name"] for model in data.get("models", [])]
    except urllib.error.URLError:
        return []

def detect_repetition(text):
    """Detect if the end of the text is stuck in a repeating pattern of words/phrases."""
    n = len(text)
    if n < 40:
        return False
    
    # Check patterns of length l from 6 up to 200 characters
    for l in range(6, min(200, n // 4)):
        pattern = text[-l:]
        # Ensure the pattern contains at least one letter to avoid false positives on markdown formatting/dividers
        if not any(c.isalpha() for c in pattern):
            continue
        match = True
        for i in range(1, 4):
            start = n - l * (i + 1)
            end = n - l * i
            if text[start:end] != pattern:
                match = False
                break
        if match:
            return True
    return False

def format_code_with_line_numbers(code_text):
    """Prepend 1-based line numbers to each line of code for accurate LLM reference."""
    lines = code_text.splitlines()
    numbered_lines = [f"{idx}: {line}" for idx, line in enumerate(lines, 1)]
    return "\n".join(numbered_lines)

def query_ollama(model, system_prompt, user_content, temperature=0.2, num_ctx=16384):
    """Send review request to local Ollama API and stream the response to stdout."""
    import time
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx
        },
        "stream": True
    }
    
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    start_time = time.time()
    stats = {}
    
    try:
        full_content = []
        print("\n--- Model Response (Streaming) ---")
        with urllib.request.urlopen(req) as response:
            for line in response:
                if line.strip():
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                        
                        # Accumulate content
                        content_piece = chunk.get("message", {}).get("content", "")
                        sys.stdout.write(content_piece)
                        sys.stdout.flush()
                        full_content.append(content_piece)
                        
                        # Capture performance metadata on done chunk
                        if chunk.get("done", False):
                            stats = {
                                "total_duration": chunk.get("total_duration"),
                                "load_duration": chunk.get("load_duration"),
                                "prompt_eval_count": chunk.get("prompt_eval_count"),
                                "prompt_eval_duration": chunk.get("prompt_eval_duration"),
                                "eval_count": chunk.get("eval_count"),
                                "eval_duration": chunk.get("eval_duration")
                            }
                        
                        # Loop detection check
                        current_text = "".join(full_content)
                        if detect_repetition(current_text):
                            print("\n\n[Warning] Loop detected in model output! Aborting stream.")
                            break
                        if len(current_text) > 40000:
                            print("\n\n[Warning] Output length limit exceeded (40k chars)! Aborting stream.")
                            break
                    except json.JSONDecodeError:
                        continue
        print("\n----------------------------------")
        
        # Display statistics
        elapsed_sec = time.time() - start_time
        print(f"Time Taken (Wall Clock): {elapsed_sec:.2f}s")
        if stats and stats.get("total_duration"):
            total_sec = stats["total_duration"] / 1e9
            prompt_tokens = stats.get("prompt_eval_count", 0)
            gen_tokens = stats.get("eval_count", 0)
            
            print(f"--- Ollama Statistics ---")
            print(f"  Total Duration: {total_sec:.2f}s")
            print(f"  Prompt Tokens: {prompt_tokens} tokens")
            if stats.get("prompt_eval_duration"):
                prompt_eval_sec = stats["prompt_eval_duration"] / 1e9
                prompt_speed = prompt_tokens / prompt_eval_sec if prompt_eval_sec > 0 else 0
                print(f"    Prompt Eval Duration: {prompt_eval_sec:.2f}s ({prompt_speed:.1f} tok/s)")
            print(f"  Response Generation: {gen_tokens} tokens")
            if stats.get("eval_duration"):
                eval_sec = stats["eval_duration"] / 1e9
                gen_speed = gen_tokens / eval_sec if eval_sec > 0 else 0
                print(f"    Generation Duration: {eval_sec:.2f}s ({gen_speed:.1f} tok/s)")
            print(f"-------------------------")
            
        return "".join(full_content)
    except urllib.error.URLError as e:
        print(f"\nError communicating with Ollama: {e}")
        return None

def find_python_files(path, scope_type):
    """Find python files depending on scope selection."""
    if scope_type == "file":
        if os.path.isfile(path) and path.endswith(".py"):
            return [path]
        return []
    
    python_files = []
    for root, dirs, files in os.walk(path):
        # Exclude directories in-place to prevent os.walk from entering them
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for file in files:
            if file.endswith(".py"):
                python_files.append(os.path.join(root, file))
    return python_files

def pick_file(initial_dir):
    """Open GUI file dialog, fallback to CLI prompt if tkinter fails/displays are missing."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.lift()
        root.attributes("-topmost", True)
        file_path = filedialog.askopenfilename(
            initialdir=initial_dir,
            title="Select Python File",
            filetypes=[("Python Files", "*.py"), ("All Files", "*.*")]
        )
        root.destroy()
        if file_path:
            return file_path
    except Exception as e:
        print(f"(GUI file picker unavailable: {e})")
    
    # Fallback
    path = input("Enter file path [relative to project root]: ").strip()
    return os.path.join(initial_dir, path)

def pick_folder(initial_dir):
    """Open GUI folder dialog, fallback to CLI prompt if tkinter fails/displays are missing."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.lift()
        root.attributes("-topmost", True)
        folder_path = filedialog.askdirectory(
            initialdir=initial_dir,
            title="Select Directory"
        )
        root.destroy()
        if folder_path:
            return folder_path
    except Exception as e:
        print(f"(GUI folder picker unavailable: {e})")
    
    # Fallback
    path = input("Enter folder path [relative to project root]: ").strip()
    return os.path.join(initial_dir, path)

def run_interactive_menu():
    """Run interactive configuration CLI."""
    print("=== Ollama Fields Code Review Agent ===")
    
    # 1. Fetch and Select Model
    models = fetch_available_models()
    if not models:
        print(f"\n[Warning] Could not connect to local Ollama. Is it running?")
        print(f"Proceeding with default model name: {DEFAULT_MODEL}")
        selected_model = DEFAULT_MODEL
    else:
        print("\nAvailable Models:")
        for idx, model in enumerate(models, 1):
            star = " (Default/Preferred)" if model == DEFAULT_MODEL else ""
            print(f"  [{idx}] {model}{star}")
        
        choice = input(f"\nSelect model index [default: {DEFAULT_MODEL}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(models):
            selected_model = models[int(choice) - 1]
        else:
            selected_model = DEFAULT_MODEL
    
    print(f"Using model: {selected_model}")

    # 2. Select Scope
    print("\nSelect Scope:")
    print("  [1] Individual File")
    print("  [2] Folder")
    print("  [3] Entire Project")
    scope_choice = input("Select option [1-3, default: 1]: ").strip()
    
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    if scope_choice == "2":
        scope_type = "folder"
        print("Opening folder picker dialog...")
        target_path = pick_folder(project_root)
        if not target_path or not os.path.exists(target_path):
            print("Invalid or no folder selected. Exiting.")
            return
    elif scope_choice == "3":
        scope_type = "project"
        target_path = project_root
    else:
        scope_type = "file"
        print("Opening file picker dialog...")
        target_path = pick_file(project_root)
        if not target_path or not os.path.exists(target_path):
            print("Invalid or no file selected. Exiting.")
            return


    # 3. Parameters
    print("\nSelect Task Type:")
    print("  [1] LLM-Optimized Code Review (Succinct, token-minimized format)")
    print("  [2] File Summarization (Functional explanation of individual files)")
    print("  [3] Integration Analysis (How selected files work/integrate together)")
    task_choice = input("Select option [1-3, default: 1]: ").strip()
    task_type = task_choice if task_choice in ["1", "2", "3"] else "1"

    temp_input = input("\nEnter temperature [0.0 - 1.0, default: 0.2]: ").strip()
    try:
        temperature = float(temp_input) if temp_input else 0.2
    except ValueError:
        temperature = 0.2

    print("\nSelect Context Window Size:")
    print("  [1] 16k (16384) [Default]")
    print("  [2] 32k (32768)")
    print("  [3] 64k (65536)")
    ctx_choice = input("Select option [1-3, default: 1]: ").strip()
    if ctx_choice == "2":
        context_length = 32768
    elif ctx_choice == "3":
        context_length = 65536
    else:
        context_length = 16384

    # 4. Output directory
    out_dir = input("\nEnter output folder for reviews/summaries [default: code_reviews]: ").strip()
    if not out_dir:
        out_dir = "code_reviews"
    out_dir_path = os.path.join(project_root, out_dir)

    # Start Processing
    files = find_python_files(target_path, scope_type)
    if not files:
        print(f"\nNo Python files found for path: {target_path}")
        return

    action_verb = "review" if task_type == "1" else ("summarize" if task_type == "2" else "analyze integration of")
    print(f"\nFound {len(files)} Python files to {action_verb}.")
    confirm = input("Proceed? (y/n) [default: y]: ").strip().lower()
    if confirm == "n":
        print("Cancelled.")
        return

    os.makedirs(out_dir_path, exist_ok=True)

    if task_type == "3":
        # Integration Analysis: combine all files into one prompt
        total_chars = sum(os.path.getsize(f) for f in files)
        estimated_tokens = total_chars // 3
        if estimated_tokens > (context_length - 4000):
            print(f"\n[Warning] Estimated token size of selected files ({estimated_tokens}) exceeds context limit ({context_length}) minus safety margin.")
            print("This could cause the model to truncate or lose details.")
            confirm_ctx = input("Do you still want to proceed? (y/n) [default: n]: ").strip().lower()
            if confirm_ctx != "y":
                return

        combined_content = []
        for filepath in files:
            rel_path = os.path.relpath(filepath, project_root)
            with open(filepath, "r", encoding="utf-8") as f:
                code = f.read()
            combined_content.append(f"### File: {rel_path}\n```python\n{code}\n```\n")
        
        user_content = "\n".join(combined_content) + "\n\nINSTRUCTION:\nPerform an integration analysis on the files provided above. Explain how they coordinate, communicate, and pass data/events, describe the hierarchy/flow of control, and note any coupling or integration risks."
        import uuid
        system_prompt = f"Request ID: {uuid.uuid4()}\n\n" + load_review_context(target_path, project_root, task_type)
        
        print("\nStarting Integration Analysis on all selected files...")
        review = query_ollama(selected_model, system_prompt, user_content, temperature, context_length)
        
        if review:
            # Determine filename
            if scope_type == "file":
                safe_name = f"integration_{os.path.basename(target_path)}.md"
            else:
                safe_name = f"integration_{os.path.basename(target_path.rstrip(os.sep))}.md"
            out_file = os.path.join(out_dir_path, safe_name)
            with open(out_file, "w", encoding="utf-8") as out_f:
                out_f.write(f"# Integration Analysis: {os.path.basename(target_path)}\n\n{review}")
            print(f"\nSaved integration analysis to: {os.path.relpath(out_file, project_root)}")
        else:
            print("Failed to run integration analysis.")
            
        del user_content
        del system_prompt
        del review
        import gc
        gc.collect()

    else:
        # Code Review or File Summarization: process file-by-file
        for idx, filepath in enumerate(files, 1):
            rel_path = os.path.relpath(filepath, project_root)
            action_name = "Reviewing" if task_type == "1" else "Summarizing"
            print(f"\n[{idx}/{len(files)}] Preparing context for: {rel_path} ...")
            
            # Load fresh context for this file
            print("-> Loading fresh rules from AGENTS.md and mapping matching skill guides...")
            import uuid
            system_prompt = f"Request ID: {uuid.uuid4()}\n\n" + load_review_context(filepath, project_root, task_type)
            
            with open(filepath, "r", encoding="utf-8") as f:
                code = f.read()

            if task_type == "1":
                numbered_code = format_code_with_line_numbers(code)
                user_content = f"File to review: {rel_path}\n\nCode with line numbers prepended:\n```python\n{numbered_code}\n```\n\nINSTRUCTION:\nPerform a token-minimized, extremely succinct code review on the code above. List ONLY violations of the repository conventions (U1, U3, U4, U6, or subsystem rules) and their fixes.\nRefer to the exact line numbers prepended to the lines above.\nFormat exactly as:\n- [RULE_ID] Line [Num]: [Brief issue] -> Fix: [Brief change]\nIf there are no violations, output 'No violations'. Do not write any conversational introduction, summary, description of the code, or greeting."
            else:
                user_content = f"File to summarize: {rel_path}\n\nCode:\n```python\n{code}\n```\n\nINSTRUCTION:\nProvide a clear, high-level, and concise functional summary of the code above. Focus on the primary responsibility of classes/functions and their major entry points. Keep the output extremely clean, structured, and token-efficient."
            
            print(f"-> Querying model for {action_name.lower()} with fresh, isolated context...")
            review = query_ollama(selected_model, system_prompt, user_content, temperature, context_length)
            
            if review:
                prefix = "codereview" if task_type == "1" else "summary"
                safe_filename = f"{prefix}_{rel_path.replace(os.sep, '_')}.md"
                out_file = os.path.join(out_dir_path, safe_filename)
                with open(out_file, "w", encoding="utf-8") as out_f:
                    title = "Code Review" if task_type == "1" else "File Summary"
                    out_f.write(f"# {title}: {rel_path}\n\n{review}")
                print(f"Saved to: {os.path.relpath(out_file, project_root)}")
            else:
                print(f"Failed to process {rel_path}")
            
            # Explicitly clear variables from memory to reset context and release resources
            del code
            del user_content
            del system_prompt
            del review
            import gc
            gc.collect()
            print(f"-> Local memory context cleared and garbage collected.")

    print(f"\nAll operations complete! Results saved in '{out_dir}'.")

if __name__ == "__main__":
    try:
        run_interactive_menu()
    except KeyboardInterrupt:
        print("\nExiting.")
        sys.exit(0)
