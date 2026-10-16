
from flask import Flask, render_template, request
from dotenv import load_dotenv
from supabase import create_client

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

import os
import uuid
import tempfile
import time

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SECRET_KEY"]
)

ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp"
}

REFERENCE_IMAGE = os.path.join(
    BASE_DIR, "static", "images", "img.jpeg"
)

COMPARISON_URL = (
    "https://www.imgonline.com.ua/eng/similarity-percent.php"
)

PROMPT_EVALUATOR_URL = (
    "https://sureprompts.com/prompt-score"
)

PROMPT_INPUT_ID = "prompt-input"

PROMPT_RESULT_XPATH = (
    '//*[@id="main-content"]/div/div/div/div[2]/div[1]/div/div/span[1]'
)


def create_browser():
    options = webdriver.ChromeOptions()

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1365,1000")

    chrome_binary = os.environ.get("CHROME_BIN")
    driver_path = os.environ.get("CHROMEDRIVER_PATH")

    if chrome_binary:
        options.binary_location = chrome_binary

    if driver_path:
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(
            service=service,
            options=options
        )
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(45)
    return driver


def compare_images(temp_path):
    if not os.path.isfile(REFERENCE_IMAGE):
        app.logger.error(
            "Reference image missing: %s", REFERENCE_IMAGE
        )
        return "Reference image not found"

    driver = None

    try:
        driver = create_browser()
        wait = WebDriverWait(driver, 30)

        driver.get(COMPARISON_URL)

        reference_input = wait.until(
            EC.presence_of_element_located(
                (By.NAME, "uploadfile")
            )
        )

        uploaded_input = wait.until(
            EC.presence_of_element_located(
                (By.NAME, "uploadfile2")
            )
        )

        reference_input.send_keys(REFERENCE_IMAGE)
        uploaded_input.send_keys(temp_path)

        submit_button = wait.until(
            EC.element_to_be_clickable((
                By.XPATH,
                '//*[@id="content"]/form/div[3]/input'
            ))
        )

        submit_button.click()

        wait.until(
            EC.url_contains("similarity-percent-result.php")
        )

        result_element = wait.until(
            EC.visibility_of_element_located(
                (By.XPATH, '//*[@id="content"]/span')
            )
        )

        result = result_element.text.strip()

        if not result:
            return "Comparison failed: empty result"

        app.logger.info("Image comparison completed")
        return result

    except Exception:
        app.logger.exception("Image comparison failed")
        return "Comparison failed"

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                app.logger.exception(
                    "Could not close image comparison browser"
                )


def evaluate_prompt(prompt):
    driver = None

    try:
        driver = create_browser()
        wait = WebDriverWait(driver, 40)

        driver.get(PROMPT_EVALUATOR_URL)

        prompt_box = wait.until(
            EC.visibility_of_element_located(
                (By.ID, PROMPT_INPUT_ID)
            )
        )

        result_element = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, PROMPT_RESULT_XPATH)
            )
        )

        previous_result = result_element.text.strip()

        prompt_box.clear()
        prompt_box.send_keys(prompt)

        # Wait for a new, non-empty result to appear and
        # remain stable for 2 seconds.
        state = {
            "text": "",
            "since": None
        }

        def result_is_ready(browser):
            elements = browser.find_elements(
                By.XPATH, PROMPT_RESULT_XPATH
            )

            if not elements:
                state["text"] = ""
                state["since"] = None
                return False

            current = elements[0].text.strip()

            if not current or current == previous_result:
                state["text"] = ""
                state["since"] = None
                return False

            if current != state["text"]:
                state["text"] = current
                state["since"] = time.monotonic()
                return False

            return (
                state["since"] is not None
                and time.monotonic() - state["since"] >= 2
            )

        wait.until(result_is_ready)

        result = state["text"].strip()

        if not result:
            return "Prompt evaluation failed: empty result"

        app.logger.info("Prompt evaluation completed")
        return result

    except TimeoutException:
        app.logger.warning(
            "Prompt evaluation timed out or result did not update"
        )
        return "Prompt evaluation timed out"

    except Exception:
        app.logger.exception("Prompt evaluation failed")
        return "Prompt evaluation failed"

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                app.logger.exception(
                    "Could not close prompt evaluation browser"
                )


def save_result(submission_id, column, result):
    try:
        supabase.table("promptia_submissions").update({
            column: result
        }).eq("id", submission_id).execute()

        app.logger.info(
            "Saved %s for submission %s",
            column, submission_id
        )
        return True

    except Exception:
        app.logger.exception(
            "Could not save %s for submission %s",
            column, submission_id
        )
        return False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/sub_1", methods=["GET", "POST"])
def sub_1():
    if request.method == "GET":
        return render_template("index.html")

    name = request.form.get("name", "").strip()
    enr = request.form.get("enr", "").strip()
    prompt = request.form.get("prompt", "").strip()
    image = request.files.get("image")

    if not name or not enr or not prompt or not image:
        return "Please complete all fields.", 400

    if (
        len(name) > 100
        or len(enr) > 50
        or len(prompt) > 10000
    ):
        return "One or more fields are too long.", 400

    extension = ALLOWED_TYPES.get(image.mimetype)

    if not extension:
        return "Upload a PNG, JPG or WebP image.", 400

    image_data = image.read()

    if not image_data or len(image_data) > 10 * 1024 * 1024:
        return "Image must be between 1 byte and 10 MB.", 400

    image_path = f"{uuid.uuid4().hex}{extension}"
    temp_path = None

    try:
        # Create a temporary image for Selenium.
        with tempfile.NamedTemporaryFile(
            suffix=extension, delete=False
        ) as temp_file:
            temp_file.write(image_data)
            temp_path = temp_file.name

        # 1. Upload image to Supabase Storage.
        supabase.storage.from_("images").upload(
            image_path,
            image_data,
            {
                "content-type": image.mimetype,
                "upsert": "false"
            }
        )

        # 2. Save submission before running evaluations.
        inserted = supabase.table(
            "promptia_submissions"
        ).insert({
            "name": name,
            "enr": enr,
            "prompt": prompt,
            "image_path": image_path,
            "similarity_result": "Processing",
            "prompt_evaluation_result": "Processing"
        }).execute()

        if not inserted.data:
            raise RuntimeError(
                "Submission insert returned no data"
            )

        submission_id = inserted.data[0]["id"]

        # 3. Run image comparison and save immediately.
        similarity_result = compare_images(temp_path)

        image_saved = save_result(
            submission_id,
            "similarity_result",
            similarity_result
        )

        # 4. Run prompt evaluation independently.
        prompt_result = evaluate_prompt(prompt)

        prompt_saved = save_result(
            submission_id,
            "prompt_evaluation_result",
            prompt_result
        )

        # The submission remains saved even if a result update fails.
        if not image_saved or not prompt_saved:
            app.logger.error(
                "One or more results were not saved for submission %s",
                submission_id
            )

        return """
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport"
                      content="width=device-width, initial-scale=1.0">
                <title>Promptia - Submission Received</title>
            </head>
            <body>
                <h2>Submission received!</h2>
                <p>Your prompt and image have been saved.</p>
                <p>Evaluation results have been processed.
                   If an evaluator failed, its status is recorded.</p>
                <a href="/">Back to form</a>
            </body>
            </html>
        """

    except Exception:
        app.logger.exception("Promptia submission failed")
        return (
            "The submission could not be completed. "
            "Please contact the organizer.",
            500
        )

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                app.logger.exception(
                    "Could not remove temporary image"
                )


@app.errorhandler(413)
def file_too_large(error):
    return "Image exceeds the 10 MB limit.", 413


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
