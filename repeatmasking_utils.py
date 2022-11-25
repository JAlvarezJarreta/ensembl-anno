# See the NOTICE file distributed with this work for additional information
# regarding copyright ownership.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import errno
import gc
import glob
import io
import logging
import math
import multiprocessing
import os
import pathlib
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile

from pathlib import Path

def run_repeatmasker_regions(
    genome_file, repeatmasker_path, library, species, main_output_dir, num_threads
):

    if not repeatmasker_path:
        repeatmasker_path = "RepeatMasker"

    if not library:
        library = "homo"

    check_exe(repeatmasker_path)
    repeatmasker_output_dir = create_dir(main_output_dir, "repeatmasker_output")
    os.chdir(repeatmasker_output_dir)

    logger.info("Skip analysis if the gtf file already exists")
    output_file = os.path.join(repeatmasker_output_dir, "annotation.gtf")
    if os.path.exists(output_file):
        transcript_count = check_gtf_content(output_file, "repeat")
        if transcript_count > 0:
            logger.info("Repeatmasker gtf file exists")
            return
    else:
        logger.info("No gtf file, go on with the analysis")

    logger.info("Creating list of genomic slices")
    seq_region_lengths = get_seq_region_lengths(genome_file, 5000)
    slice_ids = create_slice_ids(seq_region_lengths, 1000000, 0, 5000)

    if not library:
        if not species:
            species = "homo"
            generic_repeatmasker_cmd = [
                repeatmasker_path,
                "-nolow",
                "-species",
                species,
                "-engine",
                "crossmatch",
                "-dir",
                repeatmasker_output_dir,
            ]
        else:
            generic_repeatmasker_cmd = [
                repeatmasker_path,
                "-nolow",
                "-species",
                species,
                "-engine",
                "crossmatch",
                "-dir",
                repeatmasker_output_dir,
            ]

    else:
        generic_repeatmasker_cmd = [
            repeatmasker_path,
            "-nolow",
            "-lib",
            library,
            "-engine",
            "crossmatch",
            "-dir",
            repeatmasker_output_dir,
        ]

    logger.info("Running RepeatMasker")
    pool = multiprocessing.Pool(int(num_threads))
    tasks = []
    for slice_id in slice_ids:
        pool.apply_async(
            multiprocess_repeatmasker,
            args=(
                generic_repeatmasker_cmd,
                slice_id,
                genome_file,
                repeatmasker_output_dir,
            ),
        )

    pool.close()
    pool.join()
    slice_output_to_gtf(
        repeatmasker_output_dir, ".rm.gtf", 1, "repeat_id", "repeatmask"
    )


def multiprocess_repeatmasker(
    generic_repeatmasker_cmd, slice_id, genome_file, repeatmasker_output_dir
):

    region_name = slice_id[0]
    start = slice_id[1]
    end = slice_id[2]

    logger.info(
        "Processing slice to find repeats with RepeatMasker: "
        + region_name
        + ":"
        + str(start)
        + ":"
        + str(end)
    )
    seq = get_sequence(region_name, start, end, 1, genome_file, repeatmasker_output_dir)

    slice_file_name = region_name + ".rs" + str(start) + ".re" + str(end)
    region_fasta_file_name = slice_file_name + ".fa"
    region_fasta_file_path = os.path.join(
        repeatmasker_output_dir, region_fasta_file_name
    )
    region_fasta_out = open(region_fasta_file_path, "w+")
    region_fasta_out.write(">" + region_name + "\n" + seq + "\n")
    region_fasta_out.close()

    region_results_file_path = region_fasta_file_path + ".rm.gtf"
    repeatmasker_output_file_path = region_fasta_file_path + ".out"
    repeatmasker_masked_file_path = region_fasta_file_path + ".masked"
    repeatmasker_tbl_file_path = region_fasta_file_path + ".tbl"
    repeatmasker_log_file_path = region_fasta_file_path + ".log"
    repeatmasker_cat_file_path = region_fasta_file_path + ".cat"

    repeatmasker_cmd = generic_repeatmasker_cmd.copy()
    repeatmasker_cmd.append(region_fasta_file_path)
    logger.info(" ".join(repeatmasker_cmd))
    subprocess.run(repeatmasker_cmd)

    create_repeatmasker_gtf(
        repeatmasker_output_file_path, region_results_file_path, region_name
    )

    os.remove(region_fasta_file_path)
    if os.path.exists(region_results_file_path):
        os.remove(region_results_file_path)
    if os.path.exists(repeatmasker_masked_file_path):
        os.remove(repeatmasker_masked_file_path)
    if os.path.exists(repeatmasker_tbl_file_path):
        os.remove(repeatmasker_tbl_file_path)
    if os.path.exists(repeatmasker_log_file_path):
        os.remove(repeatmasker_log_file_path)
    if os.path.exists(repeatmasker_cat_file_path):
        os.remove(repeatmasker_cat_file_path)


def create_repeatmasker_gtf(
    repeatmasker_output_file_path, region_results_file_path, region_name
):

    repeatmasker_in = open(repeatmasker_output_file_path, "r")
    repeatmasker_out = open(region_results_file_path, "w+")
    line = repeatmasker_in.readline()
    repeat_count = 1
    while line:
        result_match = re.search(r"^\s*\d+\s+", line)
        if result_match:
            results = line.split()
            if results[-1] == "*":
                results.pop()
            if not len(results) == 15:
                continue

            score = results[0]
            start = results[5]
            end = results[6]
            strand = results[8]
            repeat_name = results[9]
            repeat_class = results[10]
            if strand == "+":
                repeat_start = results[11]
                repeat_end = results[12]
            else:
                repeat_start = results[13]
                repeat_end = results[12]
                strand = "-"

            gtf_line = (
                region_name
                + "\tRepeatMasker\trepeat\t"
                + str(start)
                + "\t"
                + str(end)
                + "\t.\t"
                + strand
                + "\t.\t"
                + 'repeat_id "'
                + str(repeat_count)
                + '"; repeat_name "'
                + repeat_name
                + '"; repeat_class "'
                + repeat_class
                + '"; repeat_start "'
                + str(repeat_start)
                + '"; repeat_end "'
                + str(repeat_end)
                + '"; score "'
                + str(score)
                + '";\n'
            )
            repeatmasker_out.write(gtf_line)
            repeat_count += 1
        line = repeatmasker_in.readline()
    repeatmasker_in.close()
    repeatmasker_out.close()


def run_dust_regions(genome_file, dust_path, main_output_dir, num_threads):

    if not dust_path:
        dust_path = "dustmasker"

    check_exe(dust_path)
    dust_output_dir = create_dir(main_output_dir, "dust_output")

    logger.info("Skip analysis if the gtf file already exists")
    output_file = os.path.join(dust_output_dir, "annotation.gtf")
    logger.info(output_file)
    if Path(output_file).is_file():
        transcript_count = check_gtf_content(output_file, "repeat")
        if transcript_count > 0:
            logger.info("Dust gtf file exists")
            return 0
    else:
        logger.info("No gtf file, go on with the analysis")

    logger.info("Creating list of genomic slices")
    seq_region_lengths = get_seq_region_lengths(genome_file, 5000)
    slice_ids = create_slice_ids(seq_region_lengths, 1000000, 0, 5000)

    generic_dust_cmd = [dust_path, "-in"]
    logger.info("Running Dust")
    pool = multiprocessing.Pool(int(num_threads))
    tasks = []
    for slice_id in slice_ids:
        pool.apply_async(
            multiprocess_dust,
            args=(
                generic_dust_cmd,
                slice_id,
                genome_file,
                dust_output_dir,
            ),
        )

    pool.close()
    pool.join()
    slice_output_to_gtf(dust_output_dir, ".dust.gtf", 1, "repeat_id", "dust")


def multiprocess_dust(generic_dust_cmd, slice_id, genome_file, dust_output_dir):

    region_name = slice_id[0]
    start = slice_id[1]
    end = slice_id[2]

    logger.info(
        "Processing slice to find low complexity regions with Dust: "
        + region_name
        + ":"
        + str(start)
        + ":"
        + str(end)
    )
    seq = get_sequence(region_name, start, end, 1, genome_file, dust_output_dir)

    slice_file_name = region_name + ".rs" + str(start) + ".re" + str(end)
    region_fasta_file_name = slice_file_name + ".fa"
    region_fasta_file_path = os.path.join(dust_output_dir, region_fasta_file_name)
    region_fasta_out = open(region_fasta_file_path, "w+")
    region_fasta_out.write(">" + region_name + "\n" + seq + "\n")
    region_fasta_out.close()

    region_results_file_name = slice_file_name + ".dust.gtf"
    region_results_file_path = os.path.join(dust_output_dir, region_results_file_name)

    dust_output_file_path = region_fasta_file_path + ".dust"
    dust_out = open(dust_output_file_path, "w+")
    dust_cmd = generic_dust_cmd.copy()
    dust_cmd.append(region_fasta_file_path)
    logger.info(" ".join(dust_cmd))
    subprocess.run(dust_cmd, stdout=dust_out)
    dust_out.close()

    create_dust_gtf(dust_output_file_path, region_results_file_path, region_name)
    os.remove(dust_output_file_path)
    os.remove(region_fasta_file_path)


def create_dust_gtf(dust_output_file_path, region_results_file_path, region_name):

    dust_in = open(dust_output_file_path, "r")
    dust_out = open(region_results_file_path, "w+")
    line = dust_in.readline()
    repeat_count = 1
    while line:
        result_match = re.search(r"(\d+)\ - (\d+)", line)
        if result_match:
            start = int(result_match.group(1)) + 1
            end = int(result_match.group(2)) + 1
            gtf_line = (
                region_name
                + "\tDust\trepeat\t"
                + str(start)
                + "\t"
                + str(end)
                + "\t.\t+\t.\t"
                + 'repeat_id "'
                + str(repeat_count)
                + '";\n'
            )
            dust_out.write(gtf_line)
            repeat_count += 1
        line = dust_in.readline()
    dust_in.close()
    dust_out.close()


def run_trf_repeats(genome_file, trf_path, main_output_dir, num_threads):

    if not trf_path:
        trf_path = "trf"

    check_exe(trf_path)
    trf_output_dir = create_dir(main_output_dir, "trf_output")

    logger.info("Skip analysis if the gtf file already exists")
    output_file = os.path.join(trf_output_dir, "annotation.gtf")
    if os.path.exists(output_file):
        transcript_count = check_gtf_content(output_file, "repeat")
        if transcript_count > 0:
            logger.info("Trf gtf file exists")
            return
    else:
        logger.info("No gtf file, go on with the analysis")

    logger.info("Creating list of genomic slices")
    seq_region_lengths = get_seq_region_lengths(genome_file, 5000)
    slice_ids = create_slice_ids(seq_region_lengths, 1000000, 0, 5000)

    match_score = 2
    mismatch_score = 5
    delta = 7
    pm = 80
    pi = 10
    minscore = 40
    maxperiod = 500

    trf_output_extension = (
        "."
        + str(match_score)
        + "."
        + str(mismatch_score)
        + "."
        + str(delta)
        + "."
        + str(pm)
        + "."
        + str(pi)
        + "."
        + str(minscore)
        + "."
        + str(maxperiod)
        + ".dat"
    )

    generic_trf_cmd = [
        trf_path,
        None,
        str(match_score),
        str(mismatch_score),
        str(delta),
        str(pm),
        str(pi),
        str(minscore),
        str(maxperiod),
        "-d",
        "-h",
    ]
    logger.info("Running TRF")
    pool = multiprocessing.Pool(int(num_threads))
    tasks = []
    for slice_id in slice_ids:
        pool.apply_async(
            multiprocess_trf,
            args=(
                generic_trf_cmd,
                slice_id,
                genome_file,
                trf_output_dir,
                trf_output_extension,
            ),
        )

    pool.close()
    pool.join()
    slice_output_to_gtf(trf_output_dir, ".trf.gtf", 1, "repeat_id", "trf")


def multiprocess_trf(
    generic_trf_cmd, slice_id, genome_file, trf_output_dir, trf_output_extension
):

    region_name = slice_id[0]
    start = slice_id[1]
    end = slice_id[2]

    logger.info(
        "Processing slice to find tandem repeats with TRF: "
        + region_name
        + ":"
        + str(start)
        + ":"
        + str(end)
    )
    seq = get_sequence(region_name, start, end, 1, genome_file, trf_output_dir)

    slice_file_name = region_name + ".rs" + str(start) + ".re" + str(end)
    region_fasta_file_name = slice_file_name + ".fa"
    region_fasta_file_path = os.path.join(trf_output_dir, region_fasta_file_name)
    region_fasta_out = open(region_fasta_file_path, "w+")
    region_fasta_out.write(">" + region_name + "\n" + seq + "\n")
    region_fasta_out.close()

    region_results_file_name = slice_file_name + ".trf.gtf"
    region_results_file_path = os.path.join(trf_output_dir, region_results_file_name)

    # TRF writes to the current dir, so swtich to the output dir for it
    os.chdir(trf_output_dir)
    trf_output_file_path = region_fasta_file_path + trf_output_extension
    trf_cmd = generic_trf_cmd.copy()
    trf_cmd[1] = region_fasta_file_path
    logger.info(" ".join(trf_cmd))
    subprocess.run(trf_cmd)
    create_trf_gtf(trf_output_file_path, region_results_file_path, region_name)
    os.remove(trf_output_file_path)
    os.remove(region_fasta_file_path)


def create_trf_gtf(trf_output_file_path, region_results_file_path, region_name):

    trf_in = open(trf_output_file_path, "r")
    trf_out = open(region_results_file_path, "w+")
    line = trf_in.readline()
    repeat_count = 1
    while line:
        result_match = re.search(r"^\d+", line)
        if result_match:
            results = line.split()
            if not len(results) == 15:
                continue
            start = results[0]
            end = results[1]
            period = float(results[2])
            copy_number = float(results[3])
            percent_matches = float(results[5])
            score = float(results[7])
            repeat_consensus = results[13]
            if (
                score < 50 and percent_matches >= 80 and copy_number > 2 and period < 10
            ) or (copy_number >= 2 and percent_matches >= 70 and score >= 50):
                gtf_line = (
                    region_name
                    + "\tTRF\trepeat\t"
                    + str(start)
                    + "\t"
                    + str(end)
                    + "\t.\t+\t.\t"
                    + 'repeat_id "'
                    + str(repeat_count)
                    + '"; score "'
                    + str(score)
                    + '"; repeat_consensus "'
                    + repeat_consensus
                    + '";\n'
                )
                trf_out.write(gtf_line)
                repeat_count += 1
        line = trf_in.readline()
    trf_in.close()
    trf_out.close()

