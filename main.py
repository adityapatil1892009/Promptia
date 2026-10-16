
from flask import Flask, render_template, request
from dotenv import load_dotenv
from supabase import create_client
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

import os
import uuid
import tempfile

load_dotenv()

app = Flask(__name__, template_folder=".")
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

REFERENCE_IMAGE = os.path.abspath(
    os.path.join("static", "images", "img.jpeg")
)

COMPARISON_URL = (
    "https://www.imgonline.com.ua/eng/similarity-percent.php"
)

PROMPT_EVALUATOR_URL = "https://sureprompts.com/prompt-score"

PROMPT_INPUT_ID = "prompt-input"

PROMPT_RESULT_XPATH = (
    '//*[@id="main-content"]/div/div/div/div[2]/div[1]/div/div/span[1]'
)


def create_browser():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1365,1000")

    chrome_binary = os.environ.get("CHROME_BIN")
    if chrome_binary:
        options.binary_location = chrome_binary

    return webdriver.Chrome(options=options)


def compare_images(temp_path):
    if not os.path.isfile(REFERENCE_IMAGE):
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
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    '//*[@id="content"]/form/div[3]/input'
                )
            )
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
            driver.quit()


def evaluate_prompt(prompt):
    driver = None

    try:
        driver = create_browser()
        wait = WebDriverWait(driver, 30)

        driver.get(PROMPT_EVALUATOR_URL)

        prompt_box = wait.until(
            EC.visibility_of_element_located(
                (By.ID, PROMPT_INPUT_ID)
            )
        )

        prompt_box.clear()
        prompt_box.send_keys(prompt)

        # The site generates its output automatically.
        # Wait until the result contains actual text.
        result_element = wait.until(
            lambda browser: (
                element
                if (
                    (element := browser.find_element(
                        By.XPATH, PROMPT_RESULT_XPATH
                    )).text.strip()
                )
                else False
            )
        )

        result = result_element.text.strip()

        if not result:
            return "Prompt evaluation failed: empty result"

        app.logger.info("Prompt evaluation completed")
        return result

    except TimeoutException:
        app.logger.warning("Prompt evaluation timed out")
        return "Prompt evaluation timed out"

    except Exception:
        app.logger.exception("Prompt evaluation failed")
        return "Prompt evaluation failed"

    finally:
        if driver:
            driver.quit()


@app.route("/")
def index():
    return render_template("templates/index.html")


@app.route("/sub_1", methods=["GET", "POST"])
def sub_1():
    if request.method == "GET":
        return render_template("templates/index.html")

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
        with tempfile.NamedTemporaryFile(
            suffix=extension,
            delete=False
        ) as temp_file:
            temp_file.write(image_data)
            temp_path = temp_file.name

        # 1. Save the image in Supabase Storage.
        supabase.storage.from_("images").upload(
            image_path,
            image_data,
            {
                "content-type": image.mimetype,
                "upsert": "false"
            }
        )

        # 2. Save the submission before starting evaluations.
        inserted = supabase.table(
            "promptia_submissions"
        ).insert({
            "name": name,
            "enr": enr,
            "prompt": prompt,
            "image_path": image_path
        }).execute()

        if not inserted.data:
            raise RuntimeError(
                "Submission was not returned after database insert"
            )

        submission_id = inserted.data[0]["id"]

        # 3. Run both evaluations independently.
        similarity_result = compare_images(temp_path)
        prompt_evaluation_result = evaluate_prompt(prompt)

        # 4. Store both results on the same submission row.
        supabase.table("promptia_submissions").update({
            "similarity_result": similarity_result,
            "prompt_evaluation_result": prompt_evaluation_result
        }).eq("id", submission_id).execute()

        return """
            <h2>Submission received!</h2>
            <p>Your prompt and image have been saved.</p>
            <p>Both evaluation processes have finished or recorded
            their failure status.</p>
            <a href="/">Back to form</a>
        """

    except Exception:
        app.logger.exception("Promptia submission failed")
        return (
            "Your submission could not be completed. "
            "Please contact the organizer.",
            500
        )

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@app.errorhandler(413)
def file_too_large(error):
    return "Image exceeds the 10 MB limit.", 413


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
