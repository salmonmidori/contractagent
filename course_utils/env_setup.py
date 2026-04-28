import glob
import os
import sys

# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------

# The root folder in Google Drive where the course material lives/is cloned to.
# This assumes the student has a "BUSN30135" folder in their MyDrive.
# Adjust if the course structure is different.
COURSE_DRIVE_ROOT = 'MyDrive/BUSN30135'

# ------------------------------------------------------------------
# SECRET LOADING LOGIC
# ------------------------------------------------------------------

def parse_secrets_file(path):
    secrets = {}
    try:
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    secrets[key.strip()] = value.strip()
        return secrets
    except Exception as e:
        print(f"⚠️ Warning: Could not read file {path}: {e}")
        return {}

def find_and_load_secrets(base_search_path):
    """
    Searches for secrets.txt (or secrets*.txt) in the given path.
    Returns a dictionary of secrets and the path they were loaded from.
    """
    # Search for any text file starting with 'secrets'
    search_pattern = os.path.join(base_search_path, 'secrets*.txt')
    files = glob.glob(search_pattern)
    
    if not files:
        return {}, None

    # If multiple found, we prefer 'secrets.txt', otherwise pick the first one
    target_file = files[0]
    if len(files) > 1:
        exact_match = os.path.join(base_search_path, 'secrets.txt')
        if exact_match in files:
            target_file = exact_match
            
    return parse_secrets_file(target_file), target_file

# ------------------------------------------------------------------
# MAIN INIT FUNCTION
# ------------------------------------------------------------------

def init():
    """
    Initializes the environment:
    1. Detects if running in Google Colab.
    2. If Colab, mounts Google Drive and sets the working directory to the package root.
    3. Finds and loads 'secrets.txt' from the course root into os.environ.
    """
    IS_COLAB = 'google.colab' in sys.modules
    
    course_root = None

    if IS_COLAB:
        print("🚀 Running in Google Colab")
        from google.colab import drive
        
        # Mount Drive
        drive.mount('/content/drive')
        
        # Determine Course Context
        # We assume the user has installed this package in editable mode from the drive,
        # so we can actually find the root by looking at where *this file* is located,
        # assuming the standard structure: BUSN30135/course_utils/env_setup.py
        
        # Current file path: /content/drive/MyDrive/BUSN30135/course_utils/env_setup.py
        current_file = os.path.abspath(__file__)
        package_dir = os.path.dirname(current_file) # .../course_utils
        course_root = os.path.dirname(package_dir)  # .../BUSN30135
        
        print(f"📂 Course Root determined as: {course_root}")

    else:
        print("💻 Running Locally")
        # In local dev, we assume we are somewhere inside the project.
        # We can try to find the root relative to this file as well.
        current_file = os.path.abspath(__file__)
        package_dir = os.path.dirname(current_file)
        course_root = os.path.dirname(package_dir)
        
    # --- Load Secrets ---
    print(f"🔑 Looking for secrets in: {course_root}")
    secrets, loaded_path = find_and_load_secrets(course_root)

    if not secrets:
        print("❌ Error: Could not find any file matching 'secrets*.txt' in the course root.")
    else:
        print(f"✅ Success: Secrets loaded from: {os.path.basename(loaded_path)}")
        # Inject into os.environ
        count = 0
        for key, val in secrets.items():
            # semantic check: don't overwrite existing env vars if they exist? 
            # Or force overwrite? Usually local .env/secrets.txt should win for convenience.
            os.environ[key] = val
            count += 1
        print(f"   Loaded {count} secrets into environment.")

    return course_root

def setup_lab(lab_name, check_requirements=True):
    """
    One-stop shop for setting up a lab environment.
    1. Initializes secrets and determines root.
    2. Changes CWD to the specific lab folder.
    3. (Optional) Installs requirements.txt using %pip magic.
    
    Usage in Notebook:
        from course_utils import env_setup
        env_setup.setup_lab('lab_01')
    """
    # 1. Init & Secrets
    root_path = init()
    
    if not root_path:
        print("❌ Error: Could not determine course root.")
        return
        
    # 2. Change Directory
    lab_path = os.path.join(root_path, lab_name)
    if os.path.exists(lab_path):
        os.chdir(lab_path)
        print(f"📂 Changed directory to: {lab_path}")
    else:
        print(f"⚠️ Warning: Lab folder '{lab_name}' not found in {root_path}")
        
    # 3. Install Requirements (Universal Magic)
    if check_requirements:
        req_path = os.path.join(lab_path, 'requirements.txt')
        if os.path.exists(req_path):
            print(f"📦 Found requirements.txt for {lab_name}. Installing...")
            
            try:
                # Import IPython to access magic commands
                from IPython import get_ipython
                ipython = get_ipython()
                
                if ipython:
                    # Run %pip install -r requirements.txt
                    # Note: We use the magic command to ensure it installs to the CURRENT kernel
                    ipython.run_line_magic('pip', f'install -r "{req_path}"')
                    print("✅ Dependencies installed.")
                else:
                    print("⚠️ Warning: Not running in an interactive IPython environment. Skipping pip install.")
            except ImportError:
                print("⚠️ Warning: IPython not found. Skipping pip install.")
        else:
            print(f"ℹ️ No requirements.txt found in {lab_name}. Skipping install.")
            
    return root_path
