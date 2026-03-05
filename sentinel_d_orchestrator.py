"""Sentinel-D NLP Pipeline Orchestrator."""

import os
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
import spacy
import torch
from transformers import DistilBertForSequenceClassification, DistilBertTokenizer
from huggingface_hub import hf_hub_download


class SentinelPipeline:
    """
    End-to-end NLP orchestrator for Sentinel-D DevSecOps pipeline.
    
    Combines spaCy NER (Stage 1) and DistilBERT classification (Stage 2)
    to analyze vulnerability patch requirements from security alerts.
    """
    
    # Intent classification labels (must match DistilBERT fine-tuning)
    INTENT_LABELS = {
        0: "VERSION_PIN",
        1: "API_MIGRATION",
        2: "MONKEY_PATCH",
        3: "FULL_REFACTOR"
    }
    
    # NER entity types extracted by spaCy
    NER_ENTITIES = ["VERSION_RANGE", "API_SYMBOL", "BREAKING_CHANGE", "FIX_ACTION"]
    
    def __init__(self, spacy_model_extract_dir: str = "./models/spacy_model",
                 distilbert_model_extract_dir: str = "./models/distilbert_model"):
        """
        Initialize the Sentinel Pipeline by loading both fine-tuned models.
        
        Downloads and extracts zipped models from HuggingFace Hub with fallback
        to local Windows backup paths.
        
        Args:
            spacy_model_extract_dir: Directory where spaCy NER model is extracted
            distilbert_model_extract_dir: Directory where DistilBERT model is extracted
        
        Raises:
            FileNotFoundError: If neither HF Hub nor local backup is available
        """
        print("[SentinelPipeline] Initializing NLP orchestrator...")
        
        # ============ Stage 1: Load spaCy NER Model ============
        print("[Stage 1] Loading spaCy NER model...")
        self.spacy_nlp = self._load_spacy_model(
            repo_id="mojad121/spacy-classes-finetune",
            filename="spacy-nvd-ner-v1.zip",
            local_zip_path=r"C:\Users\hp\Sentinel-d\spacy-nvd-ner-v1.zip",
            extract_dir=spacy_model_extract_dir
        )
        print("[Stage 1] ✓ spaCy NER model loaded successfully\n")
        
        # ============ Stage 2: Load DistilBERT Intent Classifier ============
        print("[Stage 2] Loading DistilBERT intent classifier...")
        distilbert_path = self._get_and_extract_model(
            repo_id="mojad121/distill-bert-intent-classifer",
            filename="distilbert-intent-classifier-v1.zip",
            local_zip_path=r"C:\Users\hp\Sentinel-d\distilbert-intent-classifier-v1.zip",
            extract_dir=distilbert_model_extract_dir
        )
        
        # Load model and tokenizer from extracted directory
        self.distilbert_model = DistilBertForSequenceClassification.from_pretrained(
            distilbert_path
        )
        self.distilbert_tokenizer = DistilBertTokenizer.from_pretrained(distilbert_path)
        
        # Set to evaluation mode (disable dropout, batch norm)
        self.distilbert_model.eval()
        
        print("[Stage 2] ✓ DistilBERT intent classifier loaded successfully\n")
        print("[SentinelPipeline] ✓ Pipeline initialization complete\n")
    
    def _get_and_extract_model(self, repo_id: str, filename: str, 
                                local_zip_path: str, extract_dir: str) -> str:
        """
        Download and extract a zipped model from HuggingFace Hub with local fallback.
        
        Implements a two-tier fallback strategy:
        1. Primary: Download from HuggingFace Hub using hf_hub_download()
        2. Fallback: Use hardcoded local_zip_path if HF download fails
        3. Error: Raise FileNotFoundError if both sources are unavailable
        
        Extraction uses Python's built-in zipfile module to decompress into extract_dir.
        
        Args:
            repo_id: HuggingFace Hub repository ID
                    (e.g., 'mojad121/spacy-classes-finetune')
            filename: Zip filename within the HF repo
                     (e.g., 'spacy-nvd-ner-v1.zip')
            local_zip_path: Absolute Windows path to local backup zip file
                           (e.g., r'C:\Users\hp\Sentinel-d\spacy-nvd-ner-v1.zip')
            extract_dir: Target directory for extraction (created if missing)
        
        Returns:
            str: Path to the extracted model directory
        
        Raises:
            FileNotFoundError: If both HF and local sources unavailable
            zipfile.BadZipFile: If the zip file is corrupted
        """
        zip_path = None
        
        # ====== Attempt 1: HuggingFace Hub Download ======
        try:
            print(f"  [Attempt 1 / HF Hub] Downloading {filename} from repo://{repo_id}...")
            zip_path = hf_hub_download(repo_id=repo_id, filename=filename)
            print(f"  [✓ Downloaded to] {zip_path}")
        except Exception as hf_error:
            print(f"  [✗ HF Download failed] {type(hf_error).__name__}: {str(hf_error)}")
            
            # ====== Attempt 2: Local Fallback ======
            print(f"  [Attempt 2 / Local Fallback] Checking {local_zip_path}...")
            if os.path.exists(local_zip_path):
                zip_path = local_zip_path
                print(f"  [✓ Using local backup] {zip_path}")
            else:
                # ====== Both sources failed ======
                error_msg = (
                    f"Model '{filename}' not found.\n"
                    f"  • HF Hub download failed: {type(hf_error).__name__}\n"
                    f"  • Local fallback missing: {local_zip_path}"
                )
                raise FileNotFoundError(error_msg)
        
        # ====== Extract Zip File ======
        extract_dir_path = Path(extract_dir)
        extract_dir_path.mkdir(parents=True, exist_ok=True)
        
        print(f"  [Extracting] {os.path.basename(zip_path)} → {extract_dir}...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # ====== Handle Nested Folder Structure ======
        # If extraction created a single nested folder, unwrap it to the parent
        contents = os.listdir(extract_dir)
        if len(contents) == 1:
            nested_path = os.path.join(extract_dir, contents[0])
            if os.path.isdir(nested_path):
                # Single nested folder detected; return the nested path
                print(f"  [✓ Nested structure detected] Using: {nested_path}")
                return str(nested_path)
        
        print(f"  [✓ Extraction complete] Model ready at {extract_dir}\n")
        return str(extract_dir_path)
    
    def _load_spacy_model(self, repo_id: str, filename: str, 
                          local_zip_path: str, extract_dir: str) -> spacy.Language:
        """
        Download, extract, and load the spaCy NER model.
        
        Uses _get_and_extract_model() for downloading/fallback logic, then loads
        the extracted model using spacy.load().
        
        Args:
            repo_id: HuggingFace Hub repository ID
            filename: Zip filename in the repo
            local_zip_path: Local backup path
            extract_dir: Extraction target directory
        
        Returns:
            Loaded spaCy Language model with NER pipeline component
        
        Raises:
            FileNotFoundError: If model cannot be loaded after extraction
        """
        model_dir = self._get_and_extract_model(repo_id, filename, local_zip_path, extract_dir)
        
        # spaCy models may be extracted to a subdirectory or root
        # Try common patterns
        possible_paths = [
            os.path.join(model_dir, "model"),
            os.path.join(model_dir, "spacy_model"),
            model_dir,
        ]
        
        nlp = None
        for path in possible_paths:
            if os.path.isdir(path):
                try:
                    nlp = spacy.load(path)
                    print(f"  [✓ spaCy model loaded] {path}\n")
                    return nlp
                except Exception as load_error:
                    print(f"  [✗ Load failed at {path}] {load_error}")
        
        raise FileNotFoundError(
            f"Could not load spaCy model from {model_dir}. "
            f"Tried: {possible_paths}"
        )
    
    def analyze_text(self, text: str) -> Dict[str, Any]:
        """
        Analyze vulnerability patch requirements using NER and intent classification.
        
        Processing pipeline:
        1. Entity Extraction (Stage 1): spaCy NER identifies VERSION_RANGE, API_SYMBOL, 
           BREAKING_CHANGE, FIX_ACTION entities in the input text.
        2. Intent Classification (Stage 2): DistilBERT predicts the repair intent 
           (VERSION_PIN, API_MIGRATION, MONKEY_PATCH, FULL_REFACTOR) with confidence score.
        
        Args:
            text: Input text describing the vulnerability or required patch
        
        Returns:
            Dictionary with structured analysis results:
            {
                "status": "success",
                "timestamp": "<iso_format_timestamp>",
                "input_text": "<original_text>",
                "analysis": {
                    "intent": {
                        "prediction": "<e.g., VERSION_PIN>",
                        "confidence": <float_0_to_1>
                    },
                    "entities": {
                        "VERSION_RANGE": ["<entity1>", "<entity2>", ...],
                        "API_SYMBOL": [...],
                        "BREAKING_CHANGE": [...],
                        "FIX_ACTION": [...]
                    }
                }
            }
        
        On error:
            {
                "status": "error",
                "timestamp": "<iso_format_timestamp>",
                "input_text": "<original_text>",
                "error": "<error_message>"
            }
        """
        try:
            # ============ Stage 1: Entity Extraction ============
            # Process text through spaCy NER pipeline
            doc = self.spacy_nlp(text)
            
            # Collect entities grouped by their label
            entities_by_label = {label: [] for label in self.NER_ENTITIES}
            for ent in doc.ents:
                if ent.label_ in entities_by_label:
                    # Add entity text (avoid duplicates if same entity appears multiple times)
                    if ent.text not in entities_by_label[ent.label_]:
                        entities_by_label[ent.label_].append(ent.text)
            
            # ============ Stage 2: Intent Classification ============
            # Tokenize input text for DistilBERT
            inputs = self.distilbert_tokenizer(
                text=text,
                truncation=True,
                padding=True,
                return_tensors="pt"
            )
            
            # Run inference (no gradient computation)
            with torch.no_grad():
                outputs = self.distilbert_model(**inputs)
                logits = outputs.logits
                
                # Compute softmax probabilities
                probabilities = torch.nn.functional.softmax(logits, dim=1)
                
                # Extract predicted class and its confidence
                predicted_class_idx = torch.argmax(probabilities, dim=-1).item()
                confidence_score = probabilities[0][predicted_class_idx].item()
            
            # Map class index to intent label
            intent_label = self.INTENT_LABELS.get(predicted_class_idx, "UNKNOWN")
            
            # ============ Build Structured Response ============
            result = {
                "status": "success",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "input_text": text,
                "analysis": {
                    "intent": {
                        "prediction": intent_label,
                        "confidence": round(confidence_score, 4)
                    },
                    "entities": entities_by_label
                }
            }
            
            return result
        
        except Exception as error:
            # Return error response maintaining schema
            return {
                "status": "error",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "input_text": text,
                "error": f"{type(error).__name__}: {str(error)}"
            }


def main():
    """
    Main execution block: instantiate the pipeline and run test analysis.
    
    Demonstrates end-to-end functionality with a realistic vulnerability
    patch scenario text, then pretty-prints the JSON-structured output.
    """
    try:
        # ============ Initialize Pipeline ============
        print("=" * 80)
        print(" SENTINEL-D NLP PIPELINE ORCHESTRATOR — INITIALIZATION")
        print("=" * 80 + "\n")
        
        pipeline = SentinelPipeline()
        
        # ============ Test Scenario ============
        # Sample text describing a Log4j vulnerability and patch strategy
        test_text = (
            "We need to migrate away from the deprecated JndiLookup class "
            "and pin the Log4j dependency to version >= 2.15.0 to patch the vulnerability."
        )
        
        print("=" * 80)
        print(" TEST SCENARIO")
        print("=" * 80)
        print(f"\n[Input Text]\n{test_text}\n")
        
        # ============ Run Analysis ============
        print("[Processing] Running Stage 1 (spaCy NER) + Stage 2 (DistilBERT)...\n")
        result = pipeline.analyze_text(test_text)
        
        # ============ Output Results ============
        print("=" * 80)
        print(" ANALYSIS OUTPUT (JSON)")
        print("=" * 80)
        print(json.dumps(result, indent=4))
        print("\n" + "=" * 80 + "\n")
        
    except Exception as error:
        print(f"\n[FATAL ERROR] {type(error).__name__}: {str(error)}\n")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()