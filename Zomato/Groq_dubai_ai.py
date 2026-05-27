"""
Enhanced Groq Vision LLaMA-4-Scout-17B Image Text Extraction Script
- Ultra-fast processing with Groq LPU inference
- Rate limiting protection (5+ second delays)
- Free API usage optimization
- Comprehensive CSV/Excel reporting
- Auto-save and error recovery
- 50 folder batch processing
"""

import os
import json
import time
import random
import argparse
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
import pandas as pd
from rich.console import Console
from rich.progress import Progress, BarColumn
from rich.table import Table
import base64
import threading
from PIL import Image
import io
from groq import Groq

# Enhanced image format support
try:
    import pillow_avif  # AVIF support
    console = Console()
    console.print("[green]✅ AVIF support loaded successfully[/green]")
    AVIF_SUPPORT = True
except ImportError:
    console = Console()
    console.print("[yellow]⚠️ AVIF support not available - install pillow-avif-plugin for full compatibility[/yellow]")
    AVIF_SUPPORT = False

# Set GROQ_API_KEY in your local environment before running.

# Configuration
SUPPORTED_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp', '.gif', '.avif']
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

# Enhanced rate limiting configuration (Conservative for free API)
RATE_LIMIT_REQUESTS_PER_MINUTE = 30   # Groq typically allows higher limits
RATE_LIMIT_REQUESTS_PER_HOUR = 1000   # Conservative hourly limit
BASE_SLEEP_BETWEEN_IMAGES = 7         # 5 seconds minimum (Groq is very fast)
ADAPTIVE_SLEEP_MULTIPLIER = 1.5       # Increase sleep if failures occur
MAX_CONSECUTIVE_FAILURES = 3          # Stop folder if too many failures

# Set the base path for Dubai menu image data.
BASE_PATH = os.getenv("ZOMATO_GROQ_IMAGE_BASE_PATH", "zomato_menu_images")

@dataclass
class ImageRecord:
    image_path: str
    folder: str
    extracted_text: Optional[str] = None
    confidence: Optional[float] = None
    processing_time: float = 0.0
    timestamp: str = ""
    error: Optional[str] = None
    model_used: str = GROQ_VISION_MODEL
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tokens_per_second: float = 0.0
    image_size_bytes: int = 0
    detected_format: str = ""

@dataclass
class TokenTracker:
    requests_per_minute: int = RATE_LIMIT_REQUESTS_PER_MINUTE
    requests_per_hour: int = RATE_LIMIT_REQUESTS_PER_HOUR
    minute_requests: List[float] = field(default_factory=list)
    hour_requests: List[float] = field(default_factory=list)
    total_tokens_used: int = 0
    total_requests: int = 0
    total_processing_time: float = 0.0

    def add_request(self, tokens_used: int = 0, processing_time: float = 0.0):
        now = time.time()
        self.minute_requests.append(now)
        self.hour_requests.append(now)
        self.total_tokens_used += tokens_used
        self.total_requests += 1
        self.total_processing_time += processing_time
        
        # Clean old requests
        self._clean_old_requests()

    def _clean_old_requests(self):
        now = time.time()
        minute_ago = now - 60
        hour_ago = now - 3600
        
        self.minute_requests = [req for req in self.minute_requests if req > minute_ago]
        self.hour_requests = [req for req in self.hour_requests if req > hour_ago]

    def can_make_request(self) -> Tuple[bool, str]:
        self._clean_old_requests()
        
        if len(self.minute_requests) >= self.requests_per_minute:
            return False, f"Rate limit: {len(self.minute_requests)}/{self.requests_per_minute} requests in last minute"
        
        if len(self.hour_requests) >= self.requests_per_hour:
            return False, f"Rate limit: {len(self.hour_requests)}/{self.requests_per_hour} requests in last hour"
        
        return True, "OK"

    def can_process_image(self) -> Tuple[bool, str]:
        return self.can_make_request()

    def get_status(self) -> Dict:
        self._clean_old_requests()
        avg_tokens_per_second = (self.total_tokens_used / max(1, self.total_processing_time)) if self.total_processing_time > 0 else 0
        return {
            "requests_last_minute": len(self.minute_requests),
            "requests_last_hour": len(self.hour_requests),
            "total_tokens": self.total_tokens_used,
            "total_requests": self.total_requests,
            "avg_tokens_per_second": avg_tokens_per_second
        }

@dataclass
class RateLimiter:
    token_tracker: TokenTracker = field(default_factory=TokenTracker)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait_if_needed(self):
        with self._lock:
            can_proceed, reason = self.token_tracker.can_make_request()
            
            if not can_proceed:
                if "Rate limit" in reason:
                    wait_time = 65  # Wait just over a minute for rate limit reset
                    console.print(f"[yellow]⏳ {reason}. Waiting {wait_time}s...[/yellow]")
                    time.sleep(wait_time)

@dataclass
class EnhancedBatchProgress:
    total_folders: int
    processed_folders: int = 0
    total_images: int = 0
    processed_images: int = 0
    successful_images: int = 0
    failed_images: int = 0
    total_tokens_used: int = 0
    folder_tokens: Dict[str, int] = field(default_factory=dict)
    folder_image_counts: Dict[str, int] = field(default_factory=dict)
    failed_folders: List[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

def adaptive_sleep_between_images(consecutive_failures: int = 0, folder_progress: float = 0.0):
    """Adaptive sleep that increases with failures and API usage"""
    base_sleep = BASE_SLEEP_BETWEEN_IMAGES
    
    # Increase sleep based on failures
    failure_multiplier = 1 + (consecutive_failures * 0.3)
    
    # Increase sleep as we progress through folder (API warming)
    progress_multiplier = 1 + (folder_progress * 0.1)
    
    # Random jitter to avoid synchronized requests
    jitter = random.uniform(0.8, 1.2)
    
    total_sleep = base_sleep * failure_multiplier * progress_multiplier * jitter
    
    console.print(f"[dim]😴 Sleeping {total_sleep:.1f}s (base: {base_sleep}s, failures: {consecutive_failures}, progress: {folder_progress:.1f})[/dim]")
    time.sleep(total_sleep)

def detect_image_format_and_convert(image_path: str) -> Tuple[str, str]:
    """
    Detect actual image format and convert to supported format if needed
    Returns: (base64_string, detected_format)
    """
    try:
        # Open image with PIL to detect actual format
        with Image.open(image_path) as img:
            # Get actual format from PIL
            actual_format = img.format.lower() if img.format else None
            
            # Convert to RGB if needed (removes transparency issues)
            if img.mode in ('RGBA', 'LA', 'P'):
                # Convert to RGB for better compatibility
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                rgb_img.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = rgb_img
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Save to memory as JPEG (most reliable format)
            img_buffer = io.BytesIO()
            img.save(img_buffer, format='JPEG', quality=95)
            img_buffer.seek(0)
            
            # Encode to base64
            base64_string = base64.b64encode(img_buffer.getvalue()).decode('utf-8')
            
            console.print(f"[dim]🖼️ Converted {actual_format or 'unknown'} to JPEG for {os.path.basename(image_path)}[/dim]")
            
            return base64_string, actual_format or "jpeg"
    
    except Exception as e:
        console.print(f"[yellow]⚠️ PIL conversion failed for {os.path.basename(image_path)}: {e}[/yellow]")
        # Fallback to direct base64 encoding
        try:
            with open(image_path, "rb") as image_file:
                base64_string = base64.b64encode(image_file.read()).decode('utf-8')
                return base64_string, "unknown"
        except Exception as e2:
            raise Exception(f"Both PIL and direct encoding failed: {e2}")

def get_image_size(image_path: str) -> int:
    """Get image file size in bytes"""
    return os.path.getsize(image_path)

def list_images_with_enhanced_validation(folder_path: str) -> List[str]:
    """Enhanced image validation with AVIF support and detailed diagnostics"""
    images = []
    skipped_details = []
    format_stats = {}
    
    if not os.path.isdir(folder_path):
        return images
    
    for file in os.listdir(folder_path):
        if any(file.lower().endswith(ext) for ext in SUPPORTED_EXTENSIONS):
            full_path = os.path.join(folder_path, file)
            
            # Check file size
            try:
                file_size = os.path.getsize(full_path)
                if file_size == 0:
                    skipped_details.append(f"{file}: Zero-byte file")
                    continue
                elif file_size < 100:
                    skipped_details.append(f"{file}: File too small ({file_size} bytes)")
                    continue
                    
            except OSError as e:
                skipped_details.append(f"{file}: File access error - {e}")
                continue
            
            # Enhanced format detection and validation
            try:
                actual_format = "unknown"
                
                # First, try to identify format by file signature
                with open(full_path, 'rb') as f:
                    header = f.read(20)
                    
                if header.startswith(b'\x89PNG'):
                    actual_format = "PNG"
                elif header.startswith(b'\xff\xd8\xff'):
                    actual_format = "JPEG"
                elif header.startswith(b'RIFF') and b'WEBP' in header:
                    actual_format = "WebP"
                elif header[4:12] == b'ftypavif':
                    actual_format = "AVIF"
                    if not AVIF_SUPPORT:
                        skipped_details.append(f"{file}: AVIF file detected but pillow-avif-plugin not installed")
                        continue
                elif header.startswith(b'BM'):
                    actual_format = "BMP"
                elif header.startswith(b'GIF87a') or header.startswith(b'GIF89a'):
                    actual_format = "GIF"
                
                # Try to open with PIL
                with Image.open(full_path) as img:
                    # Verify it's a valid image
                    img.verify()
                    
                # Re-open for actual processing (verify() closes the file)
                with Image.open(full_path) as img:
                    # Check image dimensions
                    if img.size[0] < 10 or img.size[1] < 10:
                        skipped_details.append(f"{file}: Image too small ({img.size})")
                        continue
                    
                    # Track format statistics
                    format_stats[actual_format] = format_stats.get(actual_format, 0) + 1
                    images.append(full_path)
                    
            except Exception as e:
                error_msg = str(e)
                if "AVIF support not installed" in error_msg:
                    skipped_details.append(f"{file}: AVIF format requires pillow-avif-plugin")
                elif "cannot identify image file" in error_msg:
                    skipped_details.append(f"{file}: Unrecognized format ({actual_format})")
                else:
                    skipped_details.append(f"{file}: PIL error - {error_msg[:50]}...")
    
    # Enhanced reporting
    if format_stats:
        folder_name = os.path.basename(folder_path)
        format_summary = ", ".join([f"{fmt}: {count}" for fmt, count in format_stats.items()])
        console.print(f"[green]📊 {folder_name} - Found formats: {format_summary}[/green]")
    
    if skipped_details:
        folder_name = os.path.basename(folder_path)
        console.print(f"[yellow]⚠️ {folder_name} - Skipped {len(skipped_details)} files[/yellow]")
        # Show first few details
        for detail in skipped_details[:3]:
            console.print(f"[dim]  • {detail}[/dim]")
        if len(skipped_details) > 3:
            console.print(f"[dim]  • ... and {len(skipped_details) - 3} more[/dim]")
    
    return sorted(images)

def safe_folder_key(folder_path: str) -> str:
    """Create safe folder key for output directory"""
    folder_name = os.path.basename(folder_path.rstrip(os.sep))
    # Remove invalid characters for directory names
    safe_name = "".join(c for c in folder_name if c.isalnum() or c in ('-', '_', ' '))
    return safe_name or "unknown_folder"

def ensure_dirs(base_dir: str, folder_key: str) -> str:
    """Ensure output directory exists"""
    out_dir = os.path.join(base_dir, folder_key)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def ocr_one_image_groq_only(image_path: str, model_name: str, rate_limiter: RateLimiter) -> ImageRecord:
    """Process single image with Groq Vision LLaMA-4-Scout API"""
    start_time = time.time()
    folder = os.path.dirname(image_path)
    
    record = ImageRecord(
        image_path=image_path,
        folder=folder,
        timestamp=datetime.now().isoformat(),
        model_used=model_name,
        image_size_bytes=get_image_size(image_path)
    )
    
    try:
        # Rate limiting check
        rate_limiter.wait_if_needed()
        
        # Enhanced image encoding with format detection
        base64_image, detected_format = detect_image_format_and_convert(image_path)
        record.detected_format = detected_format
        
        # Create Groq client
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        
        # Enhanced API call for text extraction
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Extract all text from this image. Provide the extracted text exactly as it appears, maintaining formatting and structure where possible. Focus on menu items, prices, restaurant information, and any other visible text. If no text is visible, respond with 'No text detected'. Be thorough and accurate in your extraction."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ]
        
        # API call with error handling and timing
        api_start = time.time()
        
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=2000,  # Higher token limit for detailed extraction
            temperature=0
        )
        
        api_time = time.time() - api_start
        
        # Extract response data
        if response.choices and response.choices[0].message:
            record.extracted_text = response.choices[0].message.content
            record.confidence = 1.0  # Groq doesn't provide confidence scores
        
        # Token usage and performance tracking
        if hasattr(response, 'usage') and response.usage:
            record.prompt_tokens = response.usage.prompt_tokens
            record.completion_tokens = response.usage.completion_tokens
            record.total_tokens = record.prompt_tokens + record.completion_tokens
            
            # Calculate tokens per second (Groq's key metric)
            if api_time > 0:
                record.tokens_per_second = record.total_tokens / api_time
        
        # Update rate limiter
        rate_limiter.token_tracker.add_request(record.total_tokens, api_time)
        
        console.print(f"[green]⚡ {os.path.basename(image_path)}: {record.total_tokens} tokens @ {record.tokens_per_second:.0f} tok/s[/green]")
        
    except Exception as e:
        error_str = str(e)
        if "rate limit" in error_str.lower():
            record.error = f"Rate limit error: {error_str}"
            console.print(f"[red]🚫 Rate limit hit for {os.path.basename(image_path)}: {record.error}[/red]")
            time.sleep(60)  # Wait 1 minute on rate limit
        elif "api" in error_str.lower() or "bad request" in error_str.lower():
            record.error = f"API error: {error_str}"
            console.print(f"[red]❌ API error for {os.path.basename(image_path)}: {record.error}[/red]")
        else:
            record.error = f"Unexpected error: {error_str}"
            console.print(f"[red]💥 Unexpected error for {os.path.basename(image_path)}: {record.error}[/red]")
    
    finally:
        record.processing_time = time.time() - start_time
    
    return record

def convert_txt_outputs_to_structured_formats(out_dir: str, records: List[ImageRecord]):
    """Convert records to CSV and Excel formats"""
    if not records:
        return
    
    # Prepare data for DataFrame
    df_data = []
    for rec in records:
        df_data.append({
            'image_file': os.path.basename(rec.image_path),
            'folder': os.path.basename(rec.folder),
            'extracted_text': rec.extracted_text or "",
            'processing_time_seconds': rec.processing_time,
            'timestamp': rec.timestamp,
            'error': rec.error or "",
            'model_used': rec.model_used,
            'prompt_tokens': rec.prompt_tokens,
            'completion_tokens': rec.completion_tokens,
            'total_tokens': rec.total_tokens,
            'tokens_per_second': rec.tokens_per_second,
            'image_size_bytes': rec.image_size_bytes,
            'detected_format': rec.detected_format,
            'success': "Yes" if not rec.error else "No"
        })
    
    # Create DataFrame
    df = pd.DataFrame(df_data)
    
    # Save to CSV
    csv_path = os.path.join(out_dir, "extracted_text_results.csv")
    df.to_csv(csv_path, index=False, encoding='utf-8')
    
    # Save to Excel
    xlsx_path = os.path.join(out_dir, "extracted_text_results.xlsx")
    try:
        df.to_excel(xlsx_path, index=False, engine='openpyxl')
    except ImportError:
        console.print("[yellow]⚠️ openpyxl not installed, skipping Excel export[/yellow]")
    
    console.print(f"[green]💾 Structured data saved: {csv_path}[/green]")

def generate_comprehensive_summary_report(progress: EnhancedBatchProgress, output_dir: str, token_tracker: TokenTracker):
    """Generate detailed summary reports in CSV and Excel formats"""
    
    # Folder-level summary
    folder_summary_data = []
    total_elapsed = time.time() - progress.start_time
    
    for folder_name in progress.folder_tokens.keys():
        folder_summary_data.append({
            'folder_name': folder_name,
            'images_processed': progress.folder_image_counts.get(folder_name, 0),
            'tokens_used': progress.folder_tokens.get(folder_name, 0),
            'avg_tokens_per_image': progress.folder_tokens.get(folder_name, 0) / max(1, progress.folder_image_counts.get(folder_name, 1)),
            'status': 'Failed' if folder_name in progress.failed_folders else 'Success'
        })
    
    # Overall summary with Groq-specific metrics
    overall_summary = {
        'total_folders_attempted': len(progress.folder_tokens),
        'successful_folders': len(progress.folder_tokens) - len(progress.failed_folders),
        'failed_folders': len(progress.failed_folders),
        'total_images_processed': progress.processed_images,
        'successful_images': progress.successful_images,
        'failed_images': progress.failed_images,
        'success_rate_percent': (progress.successful_images / max(1, progress.processed_images)) * 100,
        'total_tokens_used': progress.total_tokens_used,
        'avg_tokens_per_image': progress.total_tokens_used / max(1, progress.successful_images),
        'processing_time_minutes': total_elapsed / 60,
        'images_per_minute': progress.processed_images / max(1, total_elapsed / 60),
        'total_api_requests': token_tracker.total_requests,
        'avg_tokens_per_second': token_tracker.get_status()['avg_tokens_per_second']
    }
    
    # Save folder summary
    if folder_summary_data:
        folder_df = pd.DataFrame(folder_summary_data)
        folder_csv_path = os.path.join(output_dir, "folder_summary_report.csv")
        folder_xlsx_path = os.path.join(output_dir, "folder_summary_report.xlsx")
        
        folder_df.to_csv(folder_csv_path, index=False)
        try:
            folder_df.to_excel(folder_xlsx_path, index=False, engine='openpyxl')
        except ImportError:
            pass
        
        console.print(f"[green]📊 Folder summary saved: {folder_csv_path}[/green]")
    
    # Save overall summary
    overall_df = pd.DataFrame([overall_summary])
    overall_csv_path = os.path.join(output_dir, "overall_batch_summary.csv")
    overall_xlsx_path = os.path.join(output_dir, "overall_batch_summary.xlsx")
    
    overall_df.to_csv(overall_csv_path, index=False)
    try:
        overall_df.to_excel(overall_xlsx_path, index=False, engine='openpyxl')
    except ImportError:
        pass
    
    console.print(f"[green]📊 Overall summary saved: {overall_csv_path}[/green]")
    
    return overall_summary

def get_all_restaurant_folders(base_path: str) -> List[str]:
    """Get all restaurant folders from base path"""
    if not os.path.isdir(base_path):
        console.print(f"[red]❌ Base path does not exist: {base_path}[/red]")
        return []
    
    folders = []
    for item in os.listdir(base_path):
        item_path = os.path.join(base_path, item)
        if os.path.isdir(item_path):
            # Check if folder contains images
            if list_images_with_enhanced_validation(item_path):
                folders.append(item_path)
    
    return sorted(folders)

def get_limited_restaurant_folders(base_path: str, max_folders: int = 50) -> List[str]:
    """Get limited number of folders for processing"""
    all_folders = get_all_restaurant_folders(base_path)
    
    # Sort folders for consistent processing order
    all_folders.sort()
    
    # Take first N folders
    limited_folders = all_folders[:max_folders]
    
    console.print(f"[cyan]📁 Selected {len(limited_folders)} folders out of {len(all_folders)} total[/cyan]")
    
    return limited_folders

def bulletproof_process_single_folder(folder_path: str, model_name: str, rate_limiter: RateLimiter, 
                                    overall_progress: EnhancedBatchProgress) -> Tuple[bool, int]:
    """Process single folder with bulletproof error handling"""
    folder_name = os.path.basename(folder_path)
    
    # Use enhanced validation
    imgs = list_images_with_enhanced_validation(folder_path)
    
    if not imgs:
        console.print(f"[yellow]📁 {folder_name}: No valid images found[/yellow]")
        return True, 0
    
    folder_key = safe_folder_key(folder_path)
    out_dir = ensure_dirs(OUTPUT_DIR, folder_key)
    jsonl_path = os.path.join(out_dir, "ocr_results.jsonl")
    
    # Skip if already processed
    if os.path.exists(jsonl_path):
        console.print(f"[dim]📁 {folder_name}: Already processed, skipping[/dim]")
        return True, len(imgs)
    
    folder_totals = {
        "images": len(imgs), "failures": 0, "successes": 0, 
        "tokens": 0, "consecutive_failures": 0
    }
    
    all_records: List[ImageRecord] = []
    
    console.print(f"[bold cyan]🍽️ Processing {folder_name} ({len(imgs)} images)[/bold cyan]")
    
    try:
        with Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            console=console,
        ) as progress_bar, open(jsonl_path, "w", encoding="utf-8") as fjl:

            task = progress_bar.add_task(f"[green]{folder_name}...", total=len(imgs))

            for img_idx, img_path in enumerate(imgs):
                # Failure threshold check
                if folder_totals["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
                    console.print(f"[red]❌ Too many consecutive failures ({folder_totals['consecutive_failures']}), skipping folder[/red]")
                    overall_progress.failed_folders.append(folder_name)
                    return False, len(all_records)
                
                # Token check
                can_process, reason = rate_limiter.token_tracker.can_process_image()
                if not can_process:
                    console.print(f"[red]🛑 Rate limit reached: {reason}[/red]")
                    return False, len(all_records)
                
                # Process image with enhanced error handling
                try:
                    rec = ocr_one_image_groq_only(img_path, model_name, rate_limiter)
                    all_records.append(rec)
                    
                    # Save individual results immediately
                    txt_name = os.path.splitext(os.path.basename(img_path))[0] + ".txt"
                    with open(os.path.join(out_dir, txt_name), "w", encoding="utf-8") as ft:
                        ft.write(rec.extracted_text or "")
                    
                    fjl.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
                    fjl.flush()  # Ensure data is written immediately
                    
                    # Update counters
                    folder_totals["tokens"] += rec.total_tokens
                    overall_progress.total_tokens_used += rec.total_tokens
                    overall_progress.processed_images += 1
                    
                    if rec.error:
                        folder_totals["failures"] += 1
                        folder_totals["consecutive_failures"] += 1
                        overall_progress.failed_images += 1
                        console.print(f"[yellow]⚠️ Image {img_idx+1}/{len(imgs)} failed: {rec.error}[/yellow]")
                    else:
                        folder_totals["successes"] += 1
                        folder_totals["consecutive_failures"] = 0  # Reset on success
                        overall_progress.successful_images += 1
                    
                    # Adaptive sleep between images (shorter for Groq's speed)
                    folder_progress = (img_idx + 1) / len(imgs)
                    adaptive_sleep_between_images(folder_totals["consecutive_failures"], folder_progress)
                    
                except Exception as e:
                    console.print(f"[red]❌ Critical error processing image {img_idx+1}: {e}[/red]")
                    folder_totals["consecutive_failures"] += 1
                    overall_progress.failed_images += 1
                    
                    # Emergency sleep on critical errors
                    time.sleep(15)
                
                progress_bar.advance(task)

    except KeyboardInterrupt:
        console.print(f"\n[yellow]⏸️ {folder_name}: Interrupted by user[/yellow]")
        overall_progress.failed_folders.append(folder_name)
        return False, len(all_records)
    
    # Update progress tracking
    overall_progress.folder_tokens[folder_name] = folder_totals["tokens"]
    overall_progress.folder_image_counts[folder_name] = len(all_records)
    
    # Generate structured outputs
    if all_records:
        convert_txt_outputs_to_structured_formats(out_dir, all_records)
    
    success_rate = (folder_totals["successes"] / max(1, len(all_records))) * 100
    avg_tokens_per_sec = sum([rec.tokens_per_second for rec in all_records if rec.tokens_per_second > 0]) / max(1, len([rec for rec in all_records if rec.tokens_per_second > 0]))
    
    console.print(
        f"[green]⚡ {folder_name}: {len(all_records)}/{len(imgs)} images, "
        f"{folder_totals['successes']} successes ({success_rate:.1f}%), "
        f"{folder_totals['tokens']:,} tokens @ {avg_tokens_per_sec:.0f} tok/s avg[/green]"
    )
    
    return True, len(all_records)

def display_progress_table(progress: EnhancedBatchProgress, token_tracker: TokenTracker):
    """Display current progress in a table"""
    table = Table(title="Groq Vision LLaMA-4-Scout Batch Processing Progress")
    
    table.add_column("Metric", justify="left", style="cyan")
    table.add_column("Value", justify="right", style="green")
    
    elapsed_time = time.time() - progress.start_time
    success_rate = (progress.successful_images / max(1, progress.processed_images)) * 100
    
    table.add_row("Folders Processed", f"{progress.processed_folders}/{progress.total_folders}")
    table.add_row("Images Processed", f"{progress.processed_images}")
    table.add_row("Successful Images", f"{progress.successful_images}")
    table.add_row("Failed Images", f"{progress.failed_images}")
    table.add_row("Success Rate", f"{success_rate:.1f}%")
    table.add_row("Total Tokens", f"{progress.total_tokens_used:,}")
    table.add_row("Avg Tokens/Sec", f"{token_tracker.get_status()['avg_tokens_per_second']:.0f}")
    table.add_row("Total API Requests", f"{token_tracker.total_requests}")
    table.add_row("Processing Time", f"{elapsed_time/60:.1f} minutes")
    table.add_row("Images/Minute", f"{progress.processed_images / max(1, elapsed_time/60):.1f}")
    
    console.print(table)

def main():
    # Show model and support status
    console.print(f"[cyan]⚡ Model: {GROQ_VISION_MODEL}[/cyan]")
    console.print(f"[cyan]🚀 Groq LPU: Ultra-fast inference speeds[/cyan]")
    console.print(f"[cyan]🖼️ Image format support:[/cyan]")
    console.print(f"[cyan]  - JPEG/PNG/WebP/BMP/GIF: ✅ Built-in[/cyan]")
    console.print(f"[cyan]  - AVIF: {'✅ Available' if AVIF_SUPPORT else '❌ Install pillow-avif-plugin'}[/cyan]")
    
    # **IMPORTANT**: Check API key
    if os.environ.get("GROQ_API_KEY", "").strip() in ("", "paste-your-groq-api-key-here"):
        console.print("[red]❌ Please replace 'paste-your-groq-api-key-here' with your actual Groq API key[/red]")
        return 1
    
    # Set global output directory
    global OUTPUT_DIR
    OUTPUT_DIR = os.getenv("ZOMATO_GROQ_OUTPUT_DIR", "./extracted_results_groq_dubai")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Use the hardcoded Dubai path
    console.print(f"[cyan]📁 Processing Dubai First 30 folders from: {BASE_PATH}[/cyan]")
    
    # Get folders to process (limited to 50 max)
    all_folders = get_limited_restaurant_folders(BASE_PATH, max_folders=50)
    
    if not all_folders:
        console.print("[red]❌ No folders with valid images found in the specified path[/red]")
        return 1
    
    # Initialize progress tracking
    overall_progress = EnhancedBatchProgress(total_folders=len(all_folders))
    rate_limiter = RateLimiter()
    
    console.print(f"[bold green]🚀 Starting Groq Vision batch processing of {len(all_folders)} folders[/bold green]")
    console.print(f"[cyan]⏱️ Sleep between images: {BASE_SLEEP_BETWEEN_IMAGES}+ seconds[/cyan]")
    console.print(f"[cyan]💰 Free API usage - optimized for Groq LPU speed[/cyan]")
    console.print(f"[cyan]🖼️ Enhanced image format handling with PIL conversion[/cyan]")
    
    try:
        for folder_idx, folder_path in enumerate(all_folders):
            console.print(f"\n[bold blue]📁 Folder {folder_idx + 1}/{len(all_folders)}: {os.path.basename(folder_path)}[/bold blue]")
            
            # Process folder
            success, images_processed = bulletproof_process_single_folder(
                folder_path, GROQ_VISION_MODEL, rate_limiter, overall_progress
            )
            
            overall_progress.processed_folders += 1
            
            # Display progress
            display_progress_table(overall_progress, rate_limiter.token_tracker)
            
            # Sleep between folders (shorter for Groq)
            if folder_idx < len(all_folders) - 1:  # Don't sleep after last folder
                console.print("[dim]⏸️ Sleeping 30s between folders...[/dim]")
                time.sleep(30)
    
    except KeyboardInterrupt:
        console.print("\n[yellow]⏸️ Batch processing interrupted by user[/yellow]")
    
    except Exception as e:
        console.print(f"\n[red]💥 Critical error during batch processing: {e}[/red]")
    
    finally:
        # Generate comprehensive summary report
        console.print("\n[bold cyan]📊 Generating summary reports...[/bold cyan]")
        summary = generate_comprehensive_summary_report(
            overall_progress, OUTPUT_DIR, rate_limiter.token_tracker
        )
        
        # Final summary
        console.print("\n[bold green]🎉 Groq Vision Batch Processing Complete![/bold green]")
        console.print(f"[green]✅ Successfully processed {summary['successful_images']} images[/green]")
        console.print(f"[yellow]⚠️ Failed: {summary['failed_images']} images[/yellow]")
        console.print(f"[cyan]🧠 Total tokens used: {summary['total_tokens_used']:,}[/cyan]")
        console.print(f"[cyan]⚡ Avg processing speed: {summary['avg_tokens_per_second']:.0f} tokens/second[/cyan]")
        console.print(f"[cyan]📡 Total API requests: {summary['total_api_requests']}[/cyan]")
        console.print(f"[cyan]⏱️ Processing time: {summary['processing_time_minutes']:.1f} minutes[/cyan]")
    
    return 0

if __name__ == "__main__":
    exit(main())
