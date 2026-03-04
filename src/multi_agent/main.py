"""
CLI entry point for local agent development and testing.

Usage:
    python -m src.multi_agent.main --query "Show me patient data"
    python -m src.multi_agent.main --interactive
    python -m src.multi_agent.main --query "Follow up" --thread-id conv-123
"""

import argparse
import sys
import uuid
from typing import Optional

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from .core.graph import create_agent_graph
from .core.config import get_config
from .core.state import get_initial_state


def run_query(query: str, thread_id: Optional[str] = None, verbose: bool = False):
    """
    Run a single query through the agent system.
    
    Args:
        query: User query string
        thread_id: Optional thread ID for multi-turn conversation
        verbose: Whether to print verbose output
    """
    try:
        # Load configuration
        if verbose:
            print("Loading configuration...")
        config = get_config()

        # Create agent graph (always compiled — MemorySaver used when no Databricks checkpointer)
        if verbose:
            print("Creating agent graph...")
        agent = create_agent_graph(config, with_checkpointer=bool(thread_id))

        # Ensure a thread_id exists so the checkpointer can track state across interrupt/resume
        thread_id = thread_id or str(uuid.uuid4())
        invoke_config = {"configurable": {"thread_id": thread_id}}

        # Prepare input
        initial_state = get_initial_state(thread_id=thread_id)
        initial_state["messages"] = [HumanMessage(content=query)]

        if verbose:
            print(f"\nQuery: {query}")
            print(f"Thread ID: {thread_id}")
            print("\nProcessing...")

        # Invoke agent — loop to handle clarification interrupts
        response = agent.invoke(initial_state, config=invoke_config)
        while response.get("__interrupt__"):
            interrupt_val = response["__interrupt__"][0].value
            print(f"\n{interrupt_val['markdown']}\n")
            user_input = input("Your response: ").strip()
            response = agent.invoke(Command(resume=user_input), config=invoke_config)

        # Print response
        if verbose:
            print("\n" + "="*80)
            print("RESPONSE")
            print("="*80)

        final_response = response.get("final_response") or response.get("meta_answer") or response.get("final_summary")
        if final_response:
            print(final_response)
        else:
            print("No response generated")

        return response
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


def run_interactive():
    """Run agent in interactive mode for multi-turn conversations."""
    print("="*80)
    print("MULTI-AGENT INTERACTIVE MODE")
    print("="*80)
    print("Type 'exit' or 'quit' to end the session")
    print("Type 'new' to start a new conversation")
    print("="*80 + "\n")
    
    # Load configuration
    config = get_config()
    
    # Create agent with checkpointer
    agent = create_agent_graph(config, with_checkpointer=True)
    
    thread_id = None
    turn_count = 0
    
    while True:
        try:
            # Get user input
            if turn_count == 0:
                query = input("\n🧑 You: ").strip()
            else:
                query = input(f"\n🧑 You (turn {turn_count + 1}): ").strip()
            
            if not query:
                continue
            
            # Check for exit
            if query.lower() in ['exit', 'quit']:
                print("\nGoodbye!")
                break
            
            # Check for new conversation
            if query.lower() == 'new':
                thread_id = None
                turn_count = 0
                print("\n✓ Starting new conversation")
                continue
            
            # First turn - create thread
            if thread_id is None:
                import uuid
                thread_id = f"interactive-{uuid.uuid4()}"
                turn_count = 0
            
            # Prepare state
            state = get_initial_state(thread_id=thread_id)
            state["messages"] = [HumanMessage(content=query)]
            
            # Invoke agent — loop to handle clarification interrupts
            print("\n🤖 Agent: Processing...")
            invoke_config = {"configurable": {"thread_id": thread_id}}
            response = agent.invoke(state, config=invoke_config)
            while response.get("__interrupt__"):
                interrupt_val = response["__interrupt__"][0].value
                print(f"\n🤖 Agent: {interrupt_val['markdown']}\n")
                user_input = input("Your response: ").strip()
                response = agent.invoke(Command(resume=user_input), config=invoke_config)

            # Print response
            final_response = response.get("final_response") or response.get("meta_answer") or response.get("final_summary")
            if final_response:
                print(f"\n🤖 Agent: {final_response}")
            
            turn_count += 1
            
        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Multi-Agent System CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single query
  python -m src.multi_agent.main --query "Show me patient demographics"
  
  # Multi-turn conversation
  python -m src.multi_agent.main --query "Show patients" --thread-id conv-123
  python -m src.multi_agent.main --query "What about medications?" --thread-id conv-123
  
  # Interactive mode
  python -m src.multi_agent.main --interactive
  
  # With verbose output
  python -m src.multi_agent.main --query "test" --verbose
        """
    )
    
    parser.add_argument(
        "--query",
        type=str,
        help="Query to run through the agent system"
    )
    parser.add_argument(
        "--thread-id",
        type=str,
        help="Thread ID for multi-turn conversation"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run in interactive mode"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose output"
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if not args.interactive and not args.query:
        parser.error("Either --query or --interactive must be specified")
    
    if args.interactive:
        run_interactive()
    else:
        run_query(args.query, args.thread_id, args.verbose)


if __name__ == "__main__":
    main()
