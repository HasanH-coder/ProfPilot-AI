export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[];

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.18";
  };
  public: {
    Tables: {
      courses: {
        Row: {
          code: string;
          created_at: string;
          description: string | null;
          id: string;
          name: string | null;
          professor_id: string;
          updated_at: string;
        };
        Insert: {
          code: string;
          created_at?: string;
          description?: string | null;
          id?: string;
          name?: string | null;
          professor_id?: string;
          updated_at?: string;
        };
        Update: {
          code?: string;
          created_at?: string;
          description?: string | null;
          id?: string;
          name?: string | null;
          professor_id?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "courses_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      documents: {
        Row: {
          category: string;
          course_id: string | null;
          created_at: string;
          exam_project_id: string | null;
          id: string;
          mime_type: string | null;
          original_name: string;
          professor_id: string;
          size_bytes: number | null;
          storage_path: string;
        };
        Insert: {
          category: string;
          course_id?: string | null;
          created_at?: string;
          exam_project_id?: string | null;
          id?: string;
          mime_type?: string | null;
          original_name: string;
          professor_id?: string;
          size_bytes?: number | null;
          storage_path: string;
        };
        Update: {
          category?: string;
          course_id?: string | null;
          created_at?: string;
          exam_project_id?: string | null;
          id?: string;
          mime_type?: string | null;
          original_name?: string;
          professor_id?: string;
          size_bytes?: number | null;
          storage_path?: string;
        };
        Relationships: [
          {
            foreignKeyName: "documents_course_id_fkey";
            columns: ["course_id"];
            isOneToOne: false;
            referencedRelation: "courses";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "documents_exam_project_id_fkey";
            columns: ["exam_project_id"];
            isOneToOne: false;
            referencedRelation: "exam_projects";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "documents_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      exam_projects: {
        Row: {
          additional_notes: string | null;
          course_id: string | null;
          created_at: string;
          difficulty: string | null;
          duration_minutes: number | null;
          easy_percentage: number | null;
          enhanced_prompt: string | null;
          exam_name: string | null;
          exam_spec: Json | null;
          generation_mode: string | null;
          hard_percentage: number | null;
          id: string;
          mcq_percentage: number | null;
          medium_percentage: number | null;
          number_of_versions: number;
          professor_id: string;
          professor_prompt: string | null;
          status: string;
          subjective_percentage: number | null;
          updated_at: string;
        };
        Insert: {
          additional_notes?: string | null;
          course_id?: string | null;
          created_at?: string;
          difficulty?: string | null;
          duration_minutes?: number | null;
          easy_percentage?: number | null;
          enhanced_prompt?: string | null;
          exam_name?: string | null;
          exam_spec?: Json | null;
          generation_mode?: string | null;
          hard_percentage?: number | null;
          id?: string;
          mcq_percentage?: number | null;
          medium_percentage?: number | null;
          number_of_versions?: number;
          professor_id?: string;
          professor_prompt?: string | null;
          status?: string;
          subjective_percentage?: number | null;
          updated_at?: string;
        };
        Update: {
          additional_notes?: string | null;
          course_id?: string | null;
          created_at?: string;
          difficulty?: string | null;
          duration_minutes?: number | null;
          easy_percentage?: number | null;
          enhanced_prompt?: string | null;
          exam_name?: string | null;
          exam_spec?: Json | null;
          generation_mode?: string | null;
          hard_percentage?: number | null;
          id?: string;
          mcq_percentage?: number | null;
          medium_percentage?: number | null;
          number_of_versions?: number;
          professor_id?: string;
          professor_prompt?: string | null;
          status?: string;
          subjective_percentage?: number | null;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "exam_projects_course_id_fkey";
            columns: ["course_id"];
            isOneToOne: false;
            referencedRelation: "courses";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "exam_projects_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      profiles: {
        Row: {
          created_at: string;
          department: string | null;
          full_name: string | null;
          id: string;
          institution: string | null;
          updated_at: string;
        };
        Insert: {
          created_at?: string;
          department?: string | null;
          full_name?: string | null;
          id: string;
          institution?: string | null;
          updated_at?: string;
        };
        Update: {
          created_at?: string;
          department?: string | null;
          full_name?: string | null;
          id?: string;
          institution?: string | null;
          updated_at?: string;
        };
        Relationships: [];
      };
    };
    Views: {
      [_ in never]: never;
    };
    Functions: {
      [_ in never]: never;
    };
    Enums: {
      [_ in never]: never;
    };
    CompositeTypes: {
      [_ in never]: never;
    };
  };
};

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">;

type DefaultSchema = DatabaseWithoutInternals[Extract<
  keyof Database,
  "public"
>];

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R;
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R;
      }
      ? R
      : never
    : never;

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    keyof DefaultSchema["Tables"] | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I;
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I;
      }
      ? I
      : never
    : never;

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    keyof DefaultSchema["Tables"] | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U;
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U;
      }
      ? U
      : never
    : never;

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    keyof DefaultSchema["Enums"] | { schema: keyof DatabaseWithoutInternals },
  EnumName extends (DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never) = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never;

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends (PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never) = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never;

export const Constants = {
  public: {
    Enums: {},
  },
} as const;
