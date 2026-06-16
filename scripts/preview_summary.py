"""Preview what librarian's summary step actually produces.

Calls the real generate_book_summary() against the configured LLM (the FP8 aux
on the Spark, per config/settings.yaml), using real text from a book in the
library. Lets you eyeball summary quality without re-indexing. Throwaway demo.

Usage:  .venv/bin/python scripts/preview_summary.py
"""
import yaml

from librarian.index import generate_book_summary, generate_chapter_summary

# Real passages from book 1 (Mastering Bitcoin), pulled via the search tool.
TITLE = "Mastering Bitcoin"
CONTENT = """Bitcoin is a collection of concepts and technologies that form the basis of a digital money ecosystem. Units of currency called bitcoins are used to store and transmit value among participants in the bitcoin network. Bitcoin users communicate with each other using the bitcoin protocol primarily via the Internet, although other transport networks can also be used. The bitcoin protocol stack, available as open source software, can be run on a wide range of computing devices, including laptops and smartphones, making the technology easily accessible.

Users can transfer bitcoins over the network to do just about anything that can be done with conventional currencies, including buy and sell goods, send money to people or organizations, or extend credit. Bitcoins can be purchased, sold, and exchanged for other currencies at specialized currency exchanges. Bitcoin in a sense is the perfect form of money for the Internet because it is fast, secure, and borderless.

Unlike traditional currencies, bitcoins are entirely virtual. There are no physical coins or even digital coins per se. The coins are implied in transactions that transfer value from sender to recipient. Users of bitcoin own keys that allow them to prove ownership of transactions in the bitcoin network, unlocking the value to spend it and transfer it to a new recipient. Those keys are often stored in a digital wallet on each user's computer.

Bitcoin is a distributed, peer-to-peer system. As such there is no "central" server or point of control. Bitcoins are created through a process called "mining," which involves competing to find solutions to a mathematical problem while processing bitcoin transactions. Any participant in the bitcoin network may operate as a miner, using their computer's processing power to verify and record transactions. Every 10 minutes on average, someone is able to validate the transactions of the past 10 minutes and is rewarded with brand new bitcoins."""

config = yaml.safe_load(open("config/settings.yaml"))
print("LLM target:", config["classification"]["api_base"], "/", config["classification"]["model"])

print("\n=== BOOK SUMMARY (generate_book_summary) ===")
print(generate_book_summary(CONTENT, TITLE, config))

print("\n=== CHAPTER SUMMARY (generate_chapter_summary) ===")
print(generate_chapter_summary(CONTENT, "Chapter 1: What Is Bitcoin?", config))
