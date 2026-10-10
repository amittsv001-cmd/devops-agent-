# File Search Agent Skills & Rules
- **Objective:** Efficiently locate files or directories within the VS Code workspace.
- **Handling Queries:** 
  - Strip unnecessary conversational words (like "where is", "the path of", "find").
  - Extract the target filename or core keyword.
- **Exclusions:** Always ignore system folders like `.git`, `venv`, `node_modules`, `__pycache__`, and `.claude`.
- **Response Format:** Return clean, relative paths formatted clearly in markdown bullet points.