"""
CLI entry point for local agent development and testing.

Usage:
    python -m src.multi_agent.main --query "Show me patient data"
    python -m src.multi_agent.main --interactive
    python -m src.multi_agent.main --query "Follow up" --thread-id conv-123
"""

import argparse
import sys
from typing import Optional

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
        
        # Create agent graph
        if verbose:
            print("Creating agent graph...")
        agent = create_agent_graph(config, with_checkpointer=bool(thread_id))
        
        # Prepare input
        initial_state = get_initial_state(thread_id=thread_id)
        initial_state["messages"] = [{"role": "user", "content": query}]
        
        if verbose:
            print(f"\nQuery: {query}")
            if thread_id:
                print(f"Thread ID: {thread_id}")
            print("\nProcessing...")
        
        # Invoke agent
        response = agent.invoke(initial_state)
        
        # Print response
        if verbose:
            print("\n" + "="*80)
            print("RESPONSE")
            print("="*80)
        
        final_response = response.get("final_response") or response.get("meta_answer")
        if final_response:
            print(final_response)
        elif response.get("pending_clarification"):
            clarification = response["pending_clarification"]
            print(f"\n{clarification['reason']}\n")
            print("Please choose:")
            for i, option in enumerate(clarification['options'], 1):
                print(f"  {i}. {option}")
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
            state["messages"] = [{"role": "user", "content": query}]
            
            # Invoke agent
            print("\n🤖 Agent: Processing...")
            response = agent.invoke(state)
            
            # Print response
            final_response = response.get("final_response") or response.get("meta_answer")
            if final_response:
                print(f"\n🤖 Agent: {final_response}")
            elif response.get("pending_clarification"):
                clarification = response["pending_clarification"]
                print(f"\n🤖 Agent: {clarification['reason']}\n")
                print("Please choose:")
                for i, option in enumerate(clarification['options'], 1):
                    print(f"  {i}. {option}")
            
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
